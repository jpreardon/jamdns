# Jam Control

Creates and destroys a Jamulus Vultr VPS and points a Porkbun DNS A record at it.

The managed VPS is identified by the configured Vultr tag. This keeps the
tool from selecting unrelated instances on the same account.

🤖 Created with an LLM 🤖

## Setup

1. Install dependencies with `uv sync`.
2. Copy `config.example.yml` to `config.yml` and fill in your Vultr and Porkbun credentials and DNS settings. Keep the instance values aligned with the Vultr plan, image and startup script you want to use. `config.yml` is gitignored.

## Usage

Synchronize the DNS record to the active instance with the configured tag:

```bash
uv run main.py dns-sync
```

Create the configured instance, wait for a public IPv4 address then update DNS:

```bash
uv run main.py instance create
```

Destroy the one configured managed instance. This deliberately leaves DNS
unchanged and requires confirmation:

```bash
uv run main.py instance destroy --managed --yes
```

To destroy a specific instance rather than the managed tagged instance, use
its Vultr UUID:

```bash
uv run main.py instance destroy --instance-id <instance-id> --yes
```

Global options:

- `--config PATH` to use a config file other than `config.yml` next to the script
- `--log-level LEVEL` to set logging verbosity (default `INFO`)

Place global options before the command, for example:

```bash
uv run main.py --log-level DEBUG instance create
```

## Instance Configuration

The `vultr.instance` section selects the managed instance:

- `region`: Vultr region ID. This project uses `ewr` for New York.
- `plan`: Vultr plan ID, such as `voc-c-4c-8gb-75s`.
- `image`: Exact Vultr OS or Marketplace application name, such as `Docker`.
- `startup_script`: Exact name of an existing Vultr startup script.
- `label` and `tag`: Instance metadata. The tag must uniquely identify this tool's instance.
- `backups`, `ddos_protection` and `enable_ipv6`: Optional Vultr provisioning settings.

The create command resolves `image` and `startup_script` through the Vultr API
before provisioning. It stops if a name does not match exactly one resource.
