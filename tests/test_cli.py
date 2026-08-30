from __future__ import annotations

from whoop_pipeline.cli import build_parser


def test_cli_parser_defaults_to_incremental_local_data() -> None:
    args = build_parser().parse_args([])

    assert args.days_back == 180
    assert str(args.data_dir) == "data"
