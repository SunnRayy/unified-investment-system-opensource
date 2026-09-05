"""Configuration loader for Huinsight."""

import logging
import os
import yaml
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _resolve_config_file(config_file: Path) -> Path:
    """Return the config to load, falling back to its committed `.example` twin.

    The real `config/*.yaml` files hold owner-specific data (local finance
    directory, display name, asset labels) and are gitignored, so a fresh clone
    and a clean Docker image have only the `.example` templates. Production is
    unaffected: Cloud Run restores the real settings.yaml from GCS at startup
    (`src/api/main.py`), and this fallback only applies before/without that.

    Falling back rather than raising is what lets `git clone && quickstart`
    work; raising here would make a first run fail on a file the newcomer has
    no way to have.

    **In cloud mode the fallback is refused.** Before the open-source split the
    real settings.yaml was tracked and therefore baked into the image, so it was
    always present; now it is gitignored and must be restored from GCS at
    startup. If that restore fails, falling back would run production on the
    committed template — a different `finance_dir`, template prompts, and 8 of
    the owner's 18 config blocks simply absent — announced by nothing louder
    than a log line. Cloud Run keeps the previous revision serving when a new
    one refuses to start, so failing here costs a failed deploy; falling back
    costs a live instance quietly running on someone else's configuration.
    """
    if config_file.exists():
        return config_file

    example = config_file.with_suffix(f".example{config_file.suffix}")
    if example.exists():
        if os.getenv("UIS_GCS_BUCKET"):
            raise FileNotFoundError(
                f"Refusing to start: {config_file} is missing and this is a cloud "
                f"deployment (UIS_GCS_BUCKET is set). The real config is restored "
                f"from GCS at startup; falling back to {example.name} would run "
                f"production on the committed template. Check that "
                f"gs://{os.environ['UIS_GCS_BUCKET']}/config/{config_file.name} "
                f"exists, or unset UIS_GCS_BUCKET to run against the template "
                f"deliberately."
            )
        logger.info(
            "Config %s not found — using the committed template %s. "
            "Copy it to %s and edit to configure your own instance.",
            config_file, example.name, config_file.name,
        )
        return example

    raise FileNotFoundError(
        f"Config file not found: {config_file} (and no template at {example})"
    )


def load_config(config_path: str = "config/settings.yaml") -> Dict[str, Any]:
    """
    Load configuration from YAML file.
    
    Args:
        config_path: Path to settings.yaml file
        
    Returns:
        Configuration dictionary
    """
    config_file = _resolve_config_file(Path(config_path))

    with open(config_file, 'r') as f:
        config = yaml.safe_load(f)

    # Cloud Run: override all source paths to uploaded files directory
    finance_dir_override = os.environ.get('UIS_FINANCE_DIR')
    if finance_dir_override and config:
        config['finance_dir'] = finance_dir_override
        # Map each reader to its own subdirectory to prevent cross-reader collision
        source_registry = config.get('source_registry', {})
        for reader_name, reader_cfg in source_registry.items():
            if isinstance(reader_cfg, dict):
                # Override data_dir regardless of current value (local paths not valid on Cloud Run)
                reader_cfg['data_dir'] = os.path.join(finance_dir_override, reader_name)

    return config


def get_subsystem_path(config: Dict[str, Any], subsystem_name: str) -> Optional[str]:
    """
    Get the filesystem path for a subsystem.
    
    Args:
        config: Loaded configuration dictionary
        subsystem_name: Name of the subsystem
        
    Returns:
        Path string or None if not found
    """
    subsystems = config.get('subsystems', {})
    subsystem = subsystems.get(subsystem_name, {})
    return subsystem.get('path')


