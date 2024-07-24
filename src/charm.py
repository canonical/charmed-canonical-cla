#!/usr/bin/env python3

import logging

import ops

# Log messages can be retrieved using juju debug-log
logger = logging.getLogger(__name__)


def STARTUP_COMMAND(port: int) -> str:
    return f"fastapi run --host=0.0.0.0 --port={port} src/main.py"


class FastAPICharm(ops.CharmBase):
    """Charm the service."""
    app_container: ops.Container

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)
        self.container = self.unit.get_container("app")

        framework.observe(self.on.config_changed, self._on_config_changed)
        framework.observe(self.on.app_pebble_ready,
                          self._update_layer_and_restart)

    def _on_config_changed(self, event: ops.ConfigChangedEvent):
        try:
            port = self.config["server-port"]
            logger.info(f"server is changing port: {port}")
            self._update_layer_and_restart()
            self._handle_ports()
        except ValueError:
            self.unit.status = ops.ErrorStatus(
                f"Invalid config.server-port value: {port}")

    def _handle_ports(self):
        port = int(self.config["server-port"])
        logger.warn(f"setting ports {port}")
        self.unit.set_ports(port)

    def _update_layer_and_restart(self, event=None) -> None:
        """Define and start a workload using the Pebble API.

        You'll need to specify the right entrypoint and environment
        configuration for your specific workload. Tip: you can see the
        standard entrypoint of an existing container using docker inspect

        Learn more about Pebble layers at https://github.com/canonical/pebble
        """

        # Learn more about statuses in the SDK docs:
        # https://juju.is/docs/sdk/constructs#heading--statuses
        self.unit.status = ops.MaintenanceStatus("Assembling Pebble layers")
        new_layer_services = self._pebble_layer.to_dict().get("services", {})
        try:
            # Get the current pebble layer config
            services = self.container.get_plan().to_dict().get("services", {})
            if services != new_layer_services:
                # Changes were made, add the new layer
                self.container.add_layer(
                    "app", self._pebble_layer, combine=True)
                logger.info(
                    f"Added updated layer 'app' to Pebble plan")
                if event and isinstance(event, ops.PebbleReadyEvent):
                    self.container.replan()
                else:
                    self.container.restart('app')
                    logger.info(f"Restarted 'app' service")

            self.unit.status = ops.ActiveStatus()
        except (ops.pebble.ConnectionError, ops.pebble.APIError):
            self.unit.status = ops.WaitingStatus(
                "Waiting for Pebble in workload container")

    @property
    def _pebble_layer(self):
        """Return a dictionary representing a Pebble layer."""
        port = int(self.config['server-port'])
        health_check_endpoint: ops.pebble.HttpDict = {
            "url": f"http://localhost:{port}/healthz"
        }
        pebble_layer: ops.pebble.LayerDict = {
            "services": {
                "app": {
                    "override": "replace",
                    "startup": "enabled",
                    "working-dir": "app",
                    "command": STARTUP_COMMAND(port),
                    "on-check-failure": {
                        # restart on checks.up failure
                        "up": 'restart'
                    },
                }
            },
            "checks": {
                "test": {
                    "override": "replace",
                    "http": health_check_endpoint
                },
                "online": {
                    "override": "replace",
                    "level": ops.pebble.CheckLevel.READY,
                    "http": health_check_endpoint
                },
                "up": {
                    "override": "replace",
                    "level": ops.pebble.CheckLevel.ALIVE,
                    "http": health_check_endpoint
                }
            }
        }
        return ops.pebble.Layer(pebble_layer)


if __name__ == "__main__":  # pragma: nocover
    ops.main(FastAPICharm)  # type: ignore
