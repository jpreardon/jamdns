#!/usr/bin/env python3
"""Create and destroy a managed Vultr instance and synchronize its DNS."""

import argparse
import logging
import sys
import time
from pathlib import Path

import requests
import yaml

VULTR_INSTANCES_URL = "https://api.vultr.com/v2/instances"
VULTR_API_URL = "https://api.vultr.com/v2"
PORKBUN_DNS_URL = "https://api.porkbun.com/api/json/v3/dns"
REQUEST_TIMEOUT_SECONDS = 15
INSTANCE_READY_TIMEOUT_SECONDS = 300
INSTANCE_POLL_SECONDS = 5
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
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("dns-sync", help="Update DNS for the managed instance.")

    instance_parser = subparsers.add_parser(
        "instance", help="Manage the configured Vultr instance."
    )
    instance_subparsers = instance_parser.add_subparsers(dest="instance_command")
    instance_subparsers.add_parser(
        "create", help="Create the instance and synchronize DNS."
    )
    destroy_parser = instance_subparsers.add_parser(
        "destroy", help="Destroy an instance without changing DNS."
    )
    instance_selection = destroy_parser.add_mutually_exclusive_group(
        required=True
    )
    instance_selection.add_argument(
        "--instance-id", help="Vultr instance ID to destroy."
    )
    instance_selection.add_argument(
        "--managed",
        action="store_true",
        help="Destroy the one instance matching the configured tag.",
    )
    destroy_parser.add_argument(
        "--yes", action="store_true", help="Confirm permanent destruction."
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


def get_instance_config(config: dict) -> dict:
    """Return and validate the Vultr instance configuration."""
    instance_config = (config.get("vultr") or {}).get("instance") or {}
    required_keys = ["region", "plan", "image", "startup_script", "label", "tag"]
    for key in required_keys:
        if not instance_config.get(key):
            raise KeyError(f"Missing config value: vultr.instance.{key}")
    return instance_config


def call_vultr(
    method: str,
    endpoint: str,
    api_key: str,
    payload: dict | None = None,
    params: dict | None = None,
) -> dict:
    """Call a Vultr API endpoint and return its parsed JSON response."""
    response = requests.request(
        method,
        f"{VULTR_API_URL}/{endpoint}",
        headers={"Authorization": f"Bearer {api_key}"},
        json=payload,
        params=params,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if not response.ok:
        try:
            error = response.json()
        except ValueError:
            error = response.text
        raise RuntimeError(
            f"Vultr returned HTTP {response.status_code}: {error}"
        )
    if not response.content:
        return {}
    return response.json()


def get_managed_instance(api_key: str, tag: str) -> dict:
    """Return the sole instance associated with the configured tag."""
    result = call_vultr(
        "GET", "instances", api_key, params={"per_page": 100}
    )
    instances = [
        instance
        for instance in result.get("instances", [])
        if tag in instance.get("tags", [])
    ]

    if not instances:
        raise RuntimeError(f"No Vultr instances found with tag {tag!r}.")
    if len(instances) > 1:
        raise RuntimeError(
            f"Expected one Vultr instance with tag {tag!r}, found {len(instances)}."
        )

    return instances[0]


def find_resource_id(
    api_key: str,
    endpoint: str,
    collection_key: str,
    resource_name: str,
    identifier_key: str = "id",
) -> str:
    """Return a named Vultr resource's requested identifier."""
    resources = call_vultr("GET", endpoint, api_key).get(collection_key, [])
    matches = [
        resource
        for resource in resources
        if resource.get("name", "").casefold() == resource_name.casefold()
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected one {collection_key.rstrip('s')} named {resource_name!r}, "
            f"found {len(matches)}."
        )
    return str(matches[0][identifier_key])


def create_instance(api_key: str, instance_config: dict) -> dict:
    """Create the configured Vultr instance and return its initial response."""
    image_name = instance_config["image"]
    payload = {
        "region": instance_config["region"],
        "plan": instance_config["plan"],
        "label": instance_config["label"],
        "tags": [instance_config["tag"]],
        "script_id": find_resource_id(
            api_key, "startup-scripts", "startup_scripts",
            instance_config["startup_script"],
        ),
    }
    for setting in ("backups", "ddos_protection", "enable_ipv6"):
        if setting in instance_config:
            payload[setting] = instance_config[setting]
    if "backups" in payload:
        payload["backups"] = "enabled" if payload["backups"] else "disabled"

    try:
        payload["os_id"] = find_resource_id(api_key, "os", "os", image_name)
    except RuntimeError:
        payload["image_id"] = find_resource_id(
            api_key,
            "applications",
            "applications",
            image_name,
            identifier_key="image_id",
        )

    result = call_vultr("POST", "instances", api_key, payload)
    return result["instance"]


def wait_for_active_instance(api_key: str, instance_id: str) -> dict:
    """Wait until an instance is active and has a public IPv4 address."""
    deadline = time.monotonic() + INSTANCE_READY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        result = call_vultr("GET", f"instances/{instance_id}", api_key)
        instance = result["instance"]
        if instance["status"] == "active" and instance.get("main_ip"):
            return instance
        if instance["status"] in {"error", "failed"}:
            raise RuntimeError(
                f"Instance {instance_id} failed (status={instance['status']})."
            )
        logger.info(
            f"Waiting for instance {instance_id} "
            f"(status={instance['status']})..."
        )
        time.sleep(INSTANCE_POLL_SECONDS)
    raise RuntimeError(
        f"Instance {instance_id} did not become active within "
        f"{INSTANCE_READY_TIMEOUT_SECONDS} seconds."
    )


def destroy_instance(api_key: str, instance_id: str) -> None:
    """Permanently destroy the explicitly selected Vultr instance."""
    call_vultr("DELETE", f"instances/{instance_id}", api_key)


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


def sync_dns(config: dict, instance: dict) -> None:
    """Update the configured DNS record from an active instance's IPv4 address."""
    dns_config = config["dns"]
    hostname = f"{dns_config['host']}.{dns_config['domain']}"
    status = instance["status"]
    ip_address = instance.get("main_ip")
    logger.info(
        f"Found instance {instance['id']} "
        f"(status={status}, ip={ip_address})"
    )
    if status != "active" or not ip_address:
        raise RuntimeError(
            f"Instance {instance['id']} is not active with a public IPv4 address."
        )
    logger.info(f"Updating {hostname} -> {ip_address}")
    update_dns_record(config["porkbun"], dns_config, ip_address)
    logger.info(f"DNS update successful: {hostname} -> {ip_address}")


def main() -> int:
    """Run the requested DNS or instance lifecycle command."""
    args = parse_args()
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    try:
        config = load_config(args.config)
        instance_config = get_instance_config(config)
        api_key = config["vultr"]["api_key"]

        if args.command == "instance" and args.instance_command == "create":
            logger.info("Creating Vultr instance...")
            instance = create_instance(api_key, instance_config)
            instance = wait_for_active_instance(api_key, instance["id"])
            sync_dns(config, instance)
        elif args.command == "instance" and args.instance_command == "destroy":
            if not args.yes:
                raise RuntimeError("Refusing to destroy without --yes.")
            if args.managed:
                instance = get_managed_instance(api_key, instance_config["tag"])
                instance_id = instance["id"]
            else:
                instance_id = args.instance_id
            logger.info(f"Destroying instance {instance_id} without changing DNS.")
            destroy_instance(api_key, instance_id)
            logger.info(f"Instance {instance_id} destruction requested.")
        else:
            logger.info("Finding managed Vultr instance...")
            instance = get_managed_instance(api_key, instance_config["tag"])
            sync_dns(config, instance)
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