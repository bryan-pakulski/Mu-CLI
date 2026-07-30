# MUCLI_CONTAINER_MONITOR_V1
from mu.container.stats import (
    ContainerStatsCollector,
    parse_pair,
    parse_percent,
    parse_size,
    select_gpu_rows,
)


def test_parse_docker_sizes():
    assert parse_size("1.5kB") == 1500
    assert parse_size("2MiB") == 2 * 1024 * 1024
    assert parse_size("1.25GiB") == int(1.25 * 1024**3)
    assert parse_size("bad") == 0


def test_parse_pairs_and_percentages():
    assert parse_pair("12.5MB / 1.2GB") == (12_500_000, 1_200_000_000)
    assert parse_percent("37.25%") == 37.25


def test_select_gpu_rows_by_index_uuid_and_all():
    rows = [
        {"index": "0", "uuid": "GPU-a"},
        {"index": "1", "uuid": "GPU-b"},
    ]
    assert select_gpu_rows(rows, "all") == rows
    assert select_gpu_rows(rows, "device=1") == [rows[1]]
    assert select_gpu_rows(rows, "GPU-a") == [rows[0]]
    assert select_gpu_rows(rows, "") == []


def test_network_rates_derive_from_previous_sample():
    collector = ContainerStatsCollector()
    metrics = {"box": {
        "status": "running",
    }}
    collector._apply_stats(metrics, [{
        "Name": "box",
        "CPUPerc": "2%",
        "MemUsage": "1MiB / 2MiB",
        "MemPerc": "50%",
        "NetIO": "100B / 200B",
        "BlockIO": "10B / 20B",
        "PIDs": "3",
    }], 10.0)
    collector._apply_stats(metrics, [{
        "Name": "box",
        "CPUPerc": "3%",
        "MemUsage": "1MiB / 2MiB",
        "MemPerc": "50%",
        "NetIO": "300B / 500B",
        "BlockIO": "10B / 20B",
        "PIDs": "3",
    }], 12.0)
    assert metrics["box"]["network_rx_bytes_per_second"] == 100.0
    assert metrics["box"]["network_tx_bytes_per_second"] == 150.0
