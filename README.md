# Jam DNS

Points a DNS A record at a Vultr VPS. This was created for a very specific need (a Jamulus server that is spun up once a week). It assumes the Vultr account has exactly one instance and uses that instance's IP address. Yes, it is a very specialized script for my particular situation. 

🤖 Created with an LLM 🤖

## Setup

1. Install dependencies with `uv sync`.
2. Copy `config.example.yml` to `config.yml` and fill in your Vultr and Porkbun credentials and DNS settings. `config.yml` is gitignored.

## Usage

```bash
uv run main.py
```

Options:

- `--config PATH` to use a config file other than `config.yml` next to the script
- `--log-level LEVEL` to set logging verbosity (default `INFO`)
