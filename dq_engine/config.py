"""Settings shared by the batch scorer and the live API scoring."""

WINDOW_MINUTES = 30
DEFAULT_INTERVAL_S = 5.0
EXPECTED_INTERVAL_S = {
    "b8:27:eb:bf:9d:51": 3.7,
    "00:0f:00:70:91:0a": 5.4,
    "1c:bf:ce:15:ec:4d": 5.4,
}
