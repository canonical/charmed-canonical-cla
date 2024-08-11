import os
from typing import TypedDict

import ops


def map_config_to_env_vars(charm: ops.CharmBase, **additional_env):
    """
    Map the config values provided in config.yaml into environment variables.

    After that, the vars can be passed directly to the pebble layer.
    Variables must match the form <Key1>_<key2>_<key3>...
    """
    env_mapped_config = {
        k.replace("-", "_").replace(".", "_").upper(): v for k, v in charm.config.items()
    }

    return {**env_mapped_config, **additional_env}


ProxyDict = TypedDict("ProxyDict", {"http_proxy": str, "https_proxy": str, "no_proxy": str})


def get_proxy_dict(cfg) -> ProxyDict | None:
    """Generate an http proxy server configuration dictionary."""
    proxies: ProxyDict = {
        "http_proxy": cfg.get("http_proxy", "") or os.environ.get("JUJU_CHARM_HTTP_PROXY", ""),
        "https_proxy": cfg.get("https_proxy", "") or os.environ.get("JUJU_CHARM_HTTPS_PROXY", ""),
        "no_proxy": cfg.get("no_proxy", "") or os.environ.get("JUJU_CHARM_NO_PROXY", ""),
    }
    if all(v == "" for v in proxies.values()):
        return None
    return proxies
