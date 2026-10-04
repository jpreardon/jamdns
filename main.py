#!/usr/bin/env python3
"""Point a Porkbun DNS A record at the (single) Vultr instance."""

import argparse
import logging
import sys
from pathlib import Path

import requests
import yaml

VULTR_INSTANCES_URL = "https://api.vultr.com/v2/instances"
PORKBUN_DNS_URL = "https://api.porkbun.com/api/json/v3/dns"
REQUEST_TIMEOUT_SECONDS = 15
DEFAULT_CONFIG_PATH = Path(__file__).parent / "config.yml"

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="Path to the YAML config file (default: %(default)s).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Logging level (default: %(default)s).",
    )
    return parser.parse_args()


def load_config(config_path: Path) -> dict:
    """Load and validate the YAML config file."""
    if not config_path.is_file():
        raise FileNotFoundError(
            f"Config file not found: {config_path}. "
            "Copy config.example.yml to config.yml and fill it in."
        )

    with config_path.open(encoding="utf-8") as config_file:
        config = yaml.safe_load(config_file) or {}

    required_keys = {
        "vultr": ["api_key"],
        "porkbun": ["api_key", "secret_api_key"],
        "dns": ["domain", "host", "ttl"],
    }
    for section, keys in required_keys.items():
        for key in keys:
            if not (config.get(section) or {}).get(key):
                raise KeyError(f"Missing config value: {section}.{key}")

    return config


def get_vultr_instance(api_key: str) -> dict:
    """Return the only Vultr instance on the account."""
    response = requests.get(
        VULTR_INSTANCES_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    instances = response.json()["instances"]

    if not instances:
        raise RuntimeError("No Vultr instances found on this account.")
    if len(instances) > 1:
        raise RuntimeError(
            f"Expected exactly one Vultr instance, found {len(instances)}."
        )

    return instances[0]


def call_porkbun(
    endpoint: str, porkbun_config: dict, payload: dict | None = None
) -> dict:
    """POST to a Porkbun DNS endpoint and return the parsed JSON body."""
    response = requests.post(
        f"{PORKBUN_DNS_URL}/{endpoint}",
        json={
            "apikey": porkbun_config["api_key"],
            "secretapikey": porkbun_config["secret_api_key"],
            **(payload or {}),
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    # Porkbun explains failures in the JSON body, even on HTTP 4xx
    try:
        result = response.json()
    except ValueError:
        result = {"status": "ERROR", "message": response.text}

    if not response.ok or result.get("status") != "SUCCESS":
        raise RuntimeError(
            f"Porkbun returned HTTP {response.status_code}: {result}"
        )

    return result


def dns_record_exists(porkbun_config: dict, dns_config: dict) -> bool:
    """Return True if the configured A record already exists."""
    result = call_porkbun(
        f"retrieveByNameType/{dns_config['domain']}/A/{dns_config['host']}",
        porkbun_config,
    )
    return bool(result.get("records"))


def update_dns_record(
    porkbun_config: dict, dns_config: dict, ip_address: str
) -> None:
    """Edit the configured A record, creating it first if it is missing."""
    domain = dns_config["domain"]
    host = dns_config["host"]
    payload = {"content": ip_address, "ttl": str(dns_config["ttl"])}

    if dns_record_exists(porkbun_config, dns_config):
        logger.info(f"Updating existing A record for {host}.{domain}")
        call_porkbun(
            f"editByNameType/{domain}/A/{host}", porkbun_config, payload
        )
    else:
        logger.info(f"No A record for {host}.{domain}, creating it")
        call_porkbun(
            f"create/{domain}",
            porkbun_config,
            {"name": host, "type": "A", **payload},
        )


def main() -> int:
    """Look up the Vultr instance IP and update the DNS record."""
    args = parse_args()
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    try:
        config = load_config(args.config)
        dns_config = config["dns"]
        hostname = f"{dns_config['host']}.{dns_config['domain']}"

        logger.info("Finding Vultr instance...")
        instance = get_vultr_instance(config["vultr"]["api_key"])
        ip_address = instance["main_ip"]
        status = instance["status"]
        logger.info(
            f"Found instance {instance['id']} "
            f"(status={status}, ip={ip_address})"
        )
        if status != "active":
            raise RuntimeError(f"Instance is not active (status={status}).")

        logger.info(f"Updating {hostname} -> {ip_address}")
        update_dns_record(config["porkbun"], dns_config, ip_address)
        logger.info(f"DNS update successful: {hostname} -> {ip_address}")
    except (
        FileNotFoundError,
        KeyError,
        RuntimeError,
        requests.RequestException,
    ) as error:
        logger.error(error)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())