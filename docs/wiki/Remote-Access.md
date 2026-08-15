# Remote Access

This applies to SillyTavern running as this repo's own container, from the [Local-Only Setup](Local-Only-Setup.md). A separate, existing SillyTavern installation manages its own remote access however it already does.

SillyTavern is the only piece exposed beyond localhost, reachable locally or from another device over Tailscale, with basic auth and an IP whitelist on top (both configured in `config.yaml`, see [Local-Only Setup's step 2](Local-Only-Setup.md#2-set-up-sillytaverns-config)). Every other service — Qdrant, mem0, llama.cpp — stays bound to localhost only, unreachable outside the host machine even over Tailscale.

## Set up Tailscale

Install and log in to [Tailscale](https://tailscale.com) on both the host machine and whatever device (phone, laptop) should reach SillyTavern from elsewhere — [tailscale.com/download](https://tailscale.com/download). `tailscale status` on either device shows the Tailscale IPs to add to `config.yaml`'s `whitelist` array.

Note: testing from the host itself hits a Docker NAT quirk — a request to `127.0.0.1` appears internally as the Docker bridge gateway IP, not the host's own Tailscale IP. That gateway IP (`172.18.0.1` by default) is worth whitelisting too, purely for host-side testing convenience.
