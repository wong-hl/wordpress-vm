# Wordpress VM

Installation and set up steps

1. Install Docker on VM - [using the apt repository](https://docs.docker.com/engine/install/ubuntu/#install-using-the-repository)
2. Follow Docker [post-installaiton steps](https://docs.docker.com/engine/install/linux-postinstall/)
3. Install [cloudflare](https://pkg.cloudflare.com/index.html#ubuntu-jammy)
4. Authentiate [cloudflare](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/get-started/create-local-tunnel/#2-authenticate-cloudflared)
5. Set up tunnel through Web UI (remotely managed tunnel) - includes executing a command in CLI that runs cloudflare as a service
6. Run `docker compose up -d` to start up wordpress
