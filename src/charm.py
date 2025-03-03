#!/usr/bin/env python3

import logging
import os
from typing import Dict, List, Optional, Tuple, cast

import ops
import yaml
from charms.data_platform_libs.v0.data_interfaces import DatabaseCreatedEvent, DatabaseRequires
from charms.grafana_k8s.v0.grafana_dashboard import GrafanaDashboardProvider
from charms.loki_k8s.v0.loki_push_api import LokiPushApiConsumer
from charms.nginx_ingress_integrator.v0.nginx_route import require_nginx_route
from charms.prometheus_k8s.v0.prometheus_scrape import MetricsEndpointProvider
from charms.redis_k8s.v0.redis import RedisRelationCharmEvents, RedisRequires
from charms.tempo_coordinator_k8s.v0.tracing import TracingEndpointRequirer

import utils

# Log messages can be retrieved using juju debug-log
logger = logging.getLogger(__name__)

SERVICE_PORT = 8000
DATABASE_NAME = "canonical-cla"


class FastAPICharm(ops.CharmBase):
    """Charm the service."""

    container: ops.Container
    on = RedisRelationCharmEvents()

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)
        # Define the charm events
        self.container = self.unit.get_container("app")
        framework.observe(self.on.config_changed, self._on_config_changed)
        framework.observe(self.on.app_pebble_ready,
                          self._update_layer_and_restart)
        framework.observe(self.on.collect_unit_status, self._on_collect_status)

        framework.observe(self.on.migrate_db_action,
                          self._on_migrate_db_action)
        framework.observe(self.on.audit_logs_action,
                          self._on_audit_logs_action)
        self.unit.open_port("tcp", SERVICE_PORT)

        # Provide ability for prometheus to be scraped by Prometheus using prometheus_scrape
        self._prometheus_scraping = MetricsEndpointProvider(
            self,
            relation_name="metrics-endpoint",
            jobs=[{"static_configs": [{"targets": [f"*:{SERVICE_PORT}"]}]}],
        )

        self._logging = LokiPushApiConsumer(self, relation_name="log-proxy")
        self.tracing = TracingEndpointRequirer(self, protocols=["otlp_http"])
        self.framework.observe(
            self.tracing.on.endpoint_changed, self._update_layer_and_restart)
        self.framework.observe(
            self.tracing.on.endpoint_removed, self._update_layer_and_restart)

        # Provide grafana dashboards over a relation interface
        self._grafana_dashboards = GrafanaDashboardProvider(
            self, relation_name="grafana-dashboard"
        )

        # Charm events defined in the database requires charm library.
        self.database = DatabaseRequires(
            self, relation_name="database", database_name=DATABASE_NAME
        )

        # Redis relation
        self.redis = RedisRequires(self, relation_name="redis")
        self.framework.observe(
            self.on.redis_relation_updated, self._on_redis_relation_changed)

        self.framework.observe(
            self.database.on.database_created, self._on_database_created)
        self.framework.observe(
            self.database.on.endpoints_changed, self._on_database_created)

        require_nginx_route(
            charm=self,
            service_hostname=self.app.name,
            service_name=self.app.name,
            service_port=SERVICE_PORT,
        )

    def _on_database_created(self, event: DatabaseCreatedEvent) -> None:
        """Event is fired when postgres database is created."""
        self._update_layer_and_restart(None)

    def _on_redis_relation_changed(self, event):
        """Handle the redis relation changed event."""
        self._update_layer_and_restart(None)

    def _on_config_changed(self, event: ops.ConfigChangedEvent):
        self._update_layer_and_restart()

    def _on_collect_status(self, event):
        (valid, message) = self.config_valid_values()
        if not valid:
            message = f"Config values are not valid: {message}"
            event.add_status(ops.BlockedStatus(message))
            logger.warning(message)
            return
        try:
            status = self.container.get_service("app")
        except (ops.pebble.APIError, ops.ModelError, ops.pebble.ConnectionError) as e:
            error_message = "Waiting for Pebble in workload container"
            event.add_status(ops.MaintenanceStatus(error_message))
            logger.warning(f"{error_message}: %s", e)
            return

        db_blocked_status = self.postgres_relation_blocked()
        if db_blocked_status:
            event.add_status(db_blocked_status)
            logger.warning(db_blocked_status.message)
            return
        elif not self.fetch_postgres_relation_data():
            # We need the charms to finish integrating.
            event.add_status(ops.WaitingStatus(
                "Waiting for database relation"))
            return
        if not self.get_relation("redis"):
            error_message = (
                "Waiting relation to redis,  run 'juju relate redis-k8s:redis canonical-cla:redis'"
            )
            event.add_status(ops.BlockedStatus(error_message))
            logger.warning(error_message)
            return
        elif not self.fetch_redis_relation_data():
            error_message = "Waiting for redis relation"
            event.add_status(ops.WaitingStatus(error_message))
            logger.warning(error_message)
            return
        elif not status.is_running():
            event.add_status(ops.MaintenanceStatus(
                "Waiting for the service to start up"))
        else:
            event.add_status(ops.ActiveStatus())

    def _update_layer_and_restart(self, event=None) -> None:
        """Define and start a workload using the Pebble API.

        You'll need to specify the right entrypoint and environment
        configuration for your specific workload. Tip: you can see the
        standard entrypoint of an existing container using docker inspect

        Learn more about Pebble layers at https://github.com/canonical/pebble
        """

        new_layer_services = self._pebble_layer.to_dict().get("services", {})
        try:
            # Get the current pebble layer config
            services = self.container.get_plan().to_dict().get("services", {})
            if services != new_layer_services:
                # Changes were made, add the new layer
                self.container.add_layer(
                    "app", self._pebble_layer, combine=True)
                logger.info(f"Added updated layer 'app' to Pebble plan")
                if event and isinstance(event, ops.PebbleReadyEvent):
                    self.container.replan()
                else:
                    self.container.restart("app")
                    logger.info(f"Restarted 'app' service")

        except (ops.pebble.ConnectionError, ops.pebble.APIError):
            logger.debug("Error updating Pebble layer", exc_info=True)

    def _on_migrate_db_action(self, event: ops.ActionEvent):
        """Handle the migrate-db action."""
        # if db relation is not available, we can't run migrations
        # db_relation = self.get_relation("database")

        # if not db_relation or not db_relation.active:
        #     event.fail("Database relation is not available or ready yet")
        #     return

        revision = event.params.get("revision", "head")
        cmd = ["alembic", "upgrade", revision]
        event.log(f"Running {' '.join(cmd)}")

        try:

            (stdout, stderr) = self.container.exec(
                cmd, environment=self.app_environment, combine_stderr=True, working_dir="/srv"
            ).wait_output()
            event.set_results(
                {
                    "result": "Migrations completed successfully",
                    "full-stdout": stdout,
                    "full-stderr": stderr,
                }
            )
        except ops.pebble.ExecError as e:
            event.fail(f"Migration command failed: {e}")
            event.set_results(
                {"full-stderr": e.stderr, "full-stdout": e.stdout})
            return
        except ops.pebble.ChangeError as e:
            event.fail(f"Failed to run migrations: {e}")
            return

    def _on_audit_logs_action(self, event: ops.ActionEvent):
        """Handle the audit-logs action."""
        try:
            since = event.params.get("since")
            until = event.params.get("until")
            cmd = ["python3", "/srv/scripts/audit_logs.py"]
            if since:
                cmd.append("--since")
                cmd.append(since)
            if until:
                cmd.append("--until")
                cmd.append(until)

            logs = self.container.exec(
                cmd, environment=self.app_environment, combine_stderr=True)
            event.set_results({"logs": logs})
        except ops.model.ModelError as e:
            event.fail(f"Failed to get logs: {e}")

    @property
    def _pebble_layer(self):
        """Return a dictionary representing a Pebble layer."""
        health_check_endpoint: ops.pebble.HttpDict = {
            "url": f"http://localhost:{SERVICE_PORT}/_status/check"
        }
        uvicorn_command = " ".join(
            [
                "uvicorn app.main:app",
                "--host 0.0.0.0",
                f"--port {SERVICE_PORT}",
                "--workers 4",
                "--proxy-headers",
                "--forwarded-allow-ips '*'",
            ]
        )

        pebble_layer: ops.pebble.LayerDict = {
            "services": {
                "app": {
                    "override": "replace",
                    "startup": "enabled",
                    "working-dir": "srv",
                    "command": uvicorn_command,
                    "environment": self.app_environment,
                    "on-check-failure": {
                        # restart on checks.up failure
                        "up": "restart"
                    },
                }
            },
            "log-targets": self.pebble_log_targets,
            "checks": {
                "test": {"override": "replace", "http": health_check_endpoint},
                "online": {
                    "override": "replace",
                    "level": ops.pebble.CheckLevel.READY,
                    "http": health_check_endpoint,
                },
                "up": {
                    "override": "replace",
                    "level": ops.pebble.CheckLevel.ALIVE,
                    "http": health_check_endpoint,
                },
            },
        }
        return ops.pebble.Layer(pebble_layer)

    @property
    def app_environment(self):
        """This property method creates a dictionary containing environment variables
        for the application. It retrieves the database authentication data by calling
        the `fetch_postgres_relation_data` method and uses it to populate the dictionary.
        If any of the values are not present, it will be set to None.
        The method returns this dictionary as output.
        """
        is_valid, message = self.config_valid_values()
        if not is_valid:
            logger.warning(message)
            return {}

        env_vars = utils.map_config_to_env_vars(self)

        # add database connection details if available
        db_data = self.fetch_postgres_relation_data()
        if not db_data:
            logger.warning("No database relation data available")
            return {}
        env_vars.update(db_data)
        redis_data = self.fetch_redis_relation_data()
        if not redis_data:
            logger.warning("No redis relation data available")
            return {}
        env_vars.update(redis_data)

        # add tracing endpoint if available
        if self.tracing.is_ready():
            tracing_endpoint = self.tracing.get_endpoint("otlp_http")
            if tracing_endpoint:
                env_vars.update(
                    {"OTEL_EXPORTER_OTLP_ENDPOINT": tracing_endpoint})

        # apply proxy settings if available
        proxy_dict = utils.get_proxy_dict(self.config)
        if proxy_dict:
            env_vars.update(proxy_dict)

        env_vars["PYTHONPATH"] = "/srv"

        return env_vars

    @property
    def pebble_log_targets(self) -> Dict[str, ops.pebble.LogTargetDict]:
        """Return a dictionary representing a Pebble log target.
        [Pebble docs](https://canonical-pebble.readthedocs-hosted.com/en/latest/reference/log-forwarding/)."""
        loki_push_api_locations = cast(List[str], [endpoint.get(
            "url") for endpoint in self._logging.loki_endpoints if endpoint.get("url")])
        if not loki_push_api_locations:
            logger.error("Loki push api not available")
            return {}
        else:
            logger.info(f"Loki push api locations: {loki_push_api_locations}")
        base_log_target = {
            "override": "replace",
            "type": "loki",
            "services": ["all"],
            "labels": {
                "product": "canonical-cla",
                "charm": "canonical-cla",
                "juju_unit": self.unit.name,
                "juju_application": self.app.name,
            }
        }
        targets = {}
        for index, location in enumerate(loki_push_api_locations):
            targets[f"loki-{index}"] = {
                **base_log_target,
                "location": location
            }

        return targets

    def config_valid_values(self) -> Tuple[bool, str]:
        """Check if the config values are valid."""
        base_dir = os.getcwd()
        try:
            config = yaml.safe_load(open(f"{base_dir}/config.yaml"))
            config_items = config.get("options", None)
            if not config_items:
                return False, "No options found in config.yaml"
            for config_name, config_meta in config_items.items():
                is_secret = config_meta.get("type") == "secret"
                config_value = self.config.get(config_name)
                if config_value is None:
                    resource_name = is_secret and "Secret" or "Config"
                    return False, f"{resource_name} value {config_name} is not set"
        except Exception as e:
            logger.error("Error reading config.yaml: %s", e)
            return False, "Error reading config.yaml"
        try:
            utils.fetch_secrets(self)
        except ValueError as e:
            return False, f"Error fetching secrets: {e}"
        return True, ""

    def postgres_relation_blocked(self) -> ops.StatusBase | None:
        secrets = utils.fetch_secrets(self)
        db_secret_provided = all(
            secrets.get(key)
            for key in ["db_host", "db_port", "db_name", "db_username", "db_password"]
        )
        if not db_secret_provided and not self.get_relation("database"):
            error_message = "Waiting relation to database,  run 'juju integrate postgresql-k8s canonical-cla' or provide db secret"
            logger.warning(error_message)
            return ops.BlockedStatus(error_message)

    def fetch_postgres_relation_data(self) -> Dict | None:
        """Fetch postgres relation data.

        This function retrieves relation data from a postgres database using
        the `fetch_relation_data` method of the `database` object. The retrieved data is
        then logged for debugging purposes, and any non-empty data is processed to extract
        endpoint information, username, and password. This processed data is then returned as
        a dictionary. If no data is retrieved, the unit is set to waiting status and
        the program exits with a zero status code."""
        db_secret = utils.fetch_secrets(self)
        if all(
            db_secret.get(key)
            for key in ["db_host", "db_port", "db_name", "db_username", "db_password"]
        ):
            return {
                "DB_HOST": db_secret["db_host"],
                "DB_PORT": db_secret["db_port"],
                "DB_USERNAME": db_secret["db_username"],
                "DB_PASSWORD": db_secret["db_password"],
                "DB_DATABASE": db_secret["db_name"],
            }
        relations = self.database.fetch_relation_data()
        if relations:
            for data in relations.values():
                if not data or not data.get("username"):
                    continue
                host, port = data["endpoints"].split(":")
                db_data = {
                    "DB_HOST": host,
                    "DB_PORT": port,
                    "DB_USERNAME": data["username"],
                    "DB_PASSWORD": data["password"],
                    "DB_DATABASE": DATABASE_NAME,
                }
                return db_data
        logger.warning("No database relation data available")
        return None

    def fetch_redis_relation_data(self) -> Dict | None:
        """Get the hostname and port from the redis relation data.

        Returns:
            Tuple with the hostname and port of the related redis
        Raises:
            MissingRedisRelationDataError if the some of redis relation data is malformed/missing
        """
        relation_data = self.redis.relation_data
        if not relation_data:
            return None
        hostname = relation_data.get("hostname")
        port = relation_data.get("port")
        if not hostname or not port:
            return None
        return {
            "REDIS_HOST": hostname,
            "REDIS_PORT": port,
        }

    def get_relation(self, name: str) -> ops.model.Relation | None:
        """A modified version of the get_relation method that returns the relation object.
        This method doesn't throw an exception if there are more that one relation with the same name.
        It returns the first active relation object that matches the name.
        If there are no relations with the given name, it returns None.
        """
        relations = self.model.relations[name]
        active_relations = [
            relation for relation in relations if relation.active]
        num_related = len(active_relations)

        if num_related == 0:
            return None
        return active_relations[0]


if __name__ == "__main__":  # pragma: nocover
    ops.main(FastAPICharm)  # type: ignore
