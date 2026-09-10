from pathlib import Path

from omniagents.backends.ink import theme_args


def test_theme_args_point_at_the_product_config_dir(tmp_path: Path):
    args = theme_args(tmp_path)
    assert args == [
        "--themes-dir",
        str(tmp_path / "themes"),
        "--settings-file",
        str(tmp_path / "ink.json"),
    ]


def test_theme_args_use_the_current_product():
    from omniagents.core.product import product_info

    config_dir = product_info().config_dir()
    assert theme_args() == theme_args(config_dir)
