"""Read nested plugin settings without relying on framework-specific dict extensions."""


def config_get(config, path, default=None):
    value = config
    for key in path.split("."):
        if not isinstance(value, dict) or key not in value:
            return default
        value = value[key]
    return value
