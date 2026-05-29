from zd_agent.config import AppConfig, LoggingConfig


def test_logging_config_defaults():
    cfg = AppConfig()
    assert cfg.logging.jsonl_enabled is True
    assert cfg.logging.log_dir == "/opt/zd-agent/logs"


def test_logging_config_override():
    lc = LoggingConfig(jsonl_enabled=False, log_dir="/tmp/x")
    assert lc.jsonl_enabled is False
    assert lc.log_dir == "/tmp/x"
