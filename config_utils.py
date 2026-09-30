"""Read nested plugin settings without relying on framework-specific dict extensions."""


def config_get(config, path, default=None):
    value = config
    for key in path.split("."):
        if not isinstance(value, dict) or key not in value:
            return default
        value = value[key]
    return value


def codex_model_name(config):
    """Use the same model and fallback for requests and user-facing messages."""
    return str(config_get(config, 'codex_settings.model') or 'gpt-6-astra')
