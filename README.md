# Canonical CLA Service (K8s Charm)

## Description

Canonical's Contributor License Agreement (CLA) API service is deployed using this Charm on K8s. The charm deploys and configures a PostgresSQL database, a Redis cluster and an NGINX ingress along side the [Canonical CLA API service](https://github.com/canonical/canonical-cla).

## Usage

The Canonical CLA service is deployed using the following Juju command:

```bash
juju deploy canonical-cla --channel edge
```

Setup the required secrets for the Canonical CLA service:

```bash
# setup secrets, copy the secret id from the output
juju add-secret canonical-cla-secret-key secret-key="$(openssl rand -hex 32)"
juju grant-secret canonical-cla-secret-key canonical-cla
# paste the secret id from the output here
juju config canonical-cla secret_key="secret:{id}"

juju add-secret canonical-cla-github github-oauth-client-id="abc" github-oauth-client-secret="def"
juju grant-secret canonical-cla-github canonical-cla
juju config canonical-cla github_oauth="secret:{id}"
```

## Integrations

The following integrations are essential in order of the Canonical CLA service to work

### Database

This will deploy the PostgreSQL charm and setup an empty database called `canonical-cla`:

```bash
juju deploy postgresql-k8s
juju integrate canonical-cla:database postgresql-k8s:database
```

### Redis

```bash
juju deploy redis-k8s
juju integrate canonical-cla redis-k8s
```

### Ingress

In order to access the service via the internet we need to setup an ingress, this project is setup to run on NGINX which can be setup using the following Juju commands:

```bash
juju deploy nginx-ingress-integrator
juju integrate canonical-cla nginx-ingress-integrator
```

### Observability

The Canonical CLA service is setup with observability support in mind, this is optional but recommended in production.

Here are the Juju commands to setup and integrate the different Observability tools using Canonical's Observability Stack bundle: [COS Lite (cos-lite)](https://charmhub.io/topics/canonical-observability-stack):

```bash
CANONICAL_CLA_MODEL=$(juju status --format json | jq -r ".model.name")

juju add-model cos-lite
juju deploy cos-lite --trust

juju offer prometheus:metrics-endpoint
juju offer loki:logging
juju offer grafana:grafana-dashboard


juju switch $CANONICAL_CLA_MODEL
juju integrate canonical-cla admin/cos-lite.grafana
juju integrate canonical-cla admin/cos-lite.loki
juju integrate canonical-cla admin/cos-lite.prometheus
```

## OCI Images

The Canonical CLA service is deployed using the following OCI image, where `{id}` is the image ID with the format `timestamp-commitsha` on [@canonical/canonical-cla](https://github.com/canonical/canonical-cla):

```
ghcr.io/canonical/canonical-cla:{id}
```

The OCI image ID is automatically updated on each commit to the `main` branch to the repository [@canonical/canonical-cla](https://github.com/canonical/canonical-cla).

## Available Actions

### Running DB Migrations

The charm provides an action to run the database migrations, this should be run on new migrations or on the first deployment:

```bash
juju run-action canonical-cla/0 migrate-db [revision (default: head)]
```

## License

The Canonical CLA K8s charm is free software, distributed under the Apache Software License, version 2.0. See [License](LICENSE) for more details.
