from pathlib import Path

from investment_assistant.cli import build_parser


def test_no_command_is_allowed_for_beginner_friendly_default() -> None:
    args = build_parser().parse_args([])

    assert args.command is None


def test_show_portfolio_uses_example_file_by_default() -> None:
    args = build_parser().parse_args(["show-portfolio"])

    assert args.file == Path("data/portfolio.example.csv")


def test_news_supports_official_first_flag() -> None:
    args = build_parser().parse_args(["news", "SPCX", "--official-first"])

    assert args.ticker == "SPCX"
    assert args.official_first is True


def test_analyse_supports_dry_run_and_force_flags() -> None:
    args = build_parser().parse_args(["analyse", "NVDA", "--dry-run", "--force"])

    assert args.ticker == "NVDA"
    assert args.dry_run is True
    assert args.force is True
    assert args.deep is False


def test_analyse_supports_deep_flag() -> None:
    args = build_parser().parse_args(["analyse", "SPCX", "--deep", "--dry-run"])

    assert args.command == "analyse"
    assert args.ticker == "SPCX"
    assert args.deep is True
    assert args.dry_run is True


def test_news_report_supports_dry_run_and_force_flags() -> None:
    args = build_parser().parse_args(["news-report", "SPCX", "--dry-run", "--force"])

    assert args.command == "news-report"
    assert args.ticker == "SPCX"
    assert args.dry_run is True
    assert args.force is True


def test_profile_supports_private_json_file() -> None:
    args = build_parser().parse_args(["profile", "SPCX", "--file", "profile.json"])

    assert args.command == "profile"
    assert args.ticker == "SPCX"
    assert args.file == Path("profile.json")


def test_validate_report_accepts_a_local_json_path() -> None:
    args = build_parser().parse_args(["validate-report", "response.json"])

    assert args.command == "validate-report"
    assert args.path == Path("response.json")


def test_add_note_supports_json_file() -> None:
    args = build_parser().parse_args(["add-note", "SPCX", "--file", "my-note.json"])

    assert args.ticker == "SPCX"
    assert args.file == Path("my-note.json")
