import os
from typing import TypedDict

import ops

from secret import Secret


def map_config_to_env_vars(charm: ops.CharmBase, **additional_env):
    """
    Map the config values provided in config.yaml into environment variables.

    After that, the vars can be passed directly to the pebble layer.
    Variables must match the form <Key1>_<key2>_<key3>...
    """
    env_mapped_config = {}
    for k, v in charm.config.items():
        if not v.startswith("secret:"):
            env_mapped_config.update({k.replace("-", "_").replace(".", "_").upper(): v})

    env_mapped_config.update(fetch_secrets(charm))

    return {**env_mapped_config, **additional_env}


def fetch_secrets(charm: ops.CharmBase):
    """
    Fetch the secrets from the model and return them as a dictionary.

    The keys are the secret names and the values are the secret values.

    :param charm: The charm instance.

    :return: A dictionary with the secret names and values.
    :raises: `ValueError` if some secrets are not found.
    """
    secrets_values = {}
    for v in charm.config.values():
        if str(v).startswith("secret:"):
            secret_value_dict = charm.model.get_secret(id=str(v)).get_content(refresh=True)
            secrets_values.update(secret_value_dict)
    parsed_secrets = Secret.parse(**secrets_values).dict()
    return {k.upper(): v for k, v in parsed_secrets.items()}


ProxyDict = TypedDict("ProxyDict", {"HTTP_PROXY": str, "HTTPS_PROXY": str, "NO_PROXY": str})


def get_proxy_dict(cfg) -> ProxyDict | None:
    """Generate an http proxy server configuration dictionary."""
    proxies: ProxyDict = {
        "HTTP_PROXY": cfg.get("http_proxy", "") or os.environ.get("JUJU_CHARM_HTTP_PROXY", ""),
        "HTTPS_PROXY": cfg.get("https_proxy", "") or os.environ.get("JUJU_CHARM_HTTPS_PROXY", ""),
        "NO_PROXY": cfg.get("no_proxy", "") or os.environ.get("JUJU_CHARM_NO_PROXY", ""),
    }
    if all(v == "" for v in proxies.values()):
        return None
    return proxies
