"""scripts/sz 的 --json flag 在子命令下必须可用（vendored 后修复点）。"""
import importlib.util
from pathlib import Path

import pytest

SZ_PATH = Path(__file__).resolve().parents[1] / "scripts" / "sz"


def _load_sz():
    from importlib.machinery import SourceFileLoader
    loader = SourceFileLoader("sz_vendored", str(SZ_PATH))
    spec = importlib.util.spec_from_loader("sz_vendored", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def test_query_accepts_json_flag_after_subcommand():
    sz = _load_sz()
    parser = sz.build_parser()
    args = parser.parse_args(["query", "SELECT 1", "--json"])
    assert args.json is True
    assert args.sql == "SELECT 1"


def test_errors_accepts_json_flag():
    sz = _load_sz()
    parser = sz.build_parser()
    args = parser.parse_args(["errors", "--env", "production", "--json"])
    assert args.json is True


def test_services_accepts_json_flag():
    sz = _load_sz()
    parser = sz.build_parser()
    args = parser.parse_args(["services", "--json"])
    assert args.json is True
