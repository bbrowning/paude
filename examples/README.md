# Paude Examples

This directory contains example projects demonstrating how to use paude with different container configurations via devcontainer.json.

## How It Works

When you run `paude` in a directory containing `.devcontainer/devcontainer.json`, paude automatically:

1. Detects the configuration file
2. Builds a custom container image based on the specified base image
3. Runs any `postCreateCommand` to set up your environment
4. Launches Claude Code inside that container

This means you can use paude with any language or toolchain while maintaining the security guarantees (network filtering, credential isolation, etc.).

## Running the Examples

Each example directory contains a `.devcontainer/devcontainer.json` that configures the container environment.

### Python Example

A Python 3.11 environment with pytest installed.

```bash
cd examples/python
paude
```

Once inside, you can verify the environment:

```bash
python --version    # Python 3.11.x
python hello.py     # prints "Hello from Python"
pytest --version    # pytest is available
```

The devcontainer.json:
```json
{
    "image": "python:3.11-slim",
    "postCreateCommand": "pip install --user pytest"
}
```

### Node.js Example

A Node.js 20 environment.

```bash
cd examples/node
paude
```

Once inside, you can verify the environment:

```bash
node --version      # v20.x.x
node hello.js       # prints "Hello from Node"
npm --version       # npm is available
```

The devcontainer.json:
```json
{
    "image": "node:20-slim"
}
```

### Go Example

A Go 1.21 environment.

```bash
cd examples/go
paude
```

Once inside, you can verify the environment:

```bash
go version          # go1.21.x
```

The devcontainer.json:
```json
{
    "image": "golang:1.21"
}
```

## Creating Your Own Configuration

To set up paude for your own project:

1. Create a `.devcontainer/devcontainer.json` in your project root
2. Specify the base image and any setup commands
3. Run `paude` from your project directory

Example for a Rust project:

```json
{
    "image": "rust:1.75",
    "postCreateCommand": "cargo build"
}
```

Example with additional packages:

```json
{
    "image": "python:3.12-slim",
    "postCreateCommand": "apt-get update && apt-get install -y git && pip install -r requirements.txt"
}
```

## Rebuilding Images

Custom images are cached based on a hash of the configuration. If you change your devcontainer.json or paude.json, paude automatically detects this and rebuilds. To force a rebuild:

```bash
paude --rebuild
```

## Supported Configuration Options

See the main [README.md](../README.md#custom-container-environments-byoc) for a complete list of supported and unsupported configuration properties.
