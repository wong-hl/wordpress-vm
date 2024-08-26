# Wordpress VM

## Installation and "deployment"

Installation and set up steps

1. Install Docker on VM - [using the apt repository](https://docs.docker.com/engine/install/ubuntu/#install-using-the-repository)
2. Follow Docker [post-installaiton steps](https://docs.docker.com/engine/install/linux-postinstall/)
3. Install [cloudflare](https://pkg.cloudflare.com/index.html#ubuntu-jammy)
4. Authentiate [cloudflare](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/get-started/create-local-tunnel/#2-authenticate-cloudflared)
5. Set up tunnel through Web UI (remotely managed tunnel) - includes executing a command in CLI that runs cloudflare as a service
6. Run `docker compose up -d` to start up wordpress

## Backup to NAS

1. Run ansible to install syncthing which [follows the installation docs](https://apt.syncthing.net/)
2. Enable syncthing as a service. `<user>` is the user to run syncthing as. To run as root use `root`

   ```console
   sudo systemctl enable syncthing@<user>.service
   sudo systemctl start syncthing@u<ser>.service
   ```

3. Syncing the directory directly is not possible due to permissions. [A way around this](https://forum.syncthing.net/t/permission-denied-backing-up-docker-mounted-volumes/19335/4) is to use `bindfs`

   ```console
   sudo bindfs -o ro -u whl -g whl /var/lib/docker/volumes/wordpress_mariadb_data syncthing/mariadb-data/
   sudo bindfs -o ro -u whl -g whl /var/lib/docker/volumes/wordpress_wordpress_data/ syncthing/wordpress-data
   ```

## Updating containers

As this is a problem that lots of people encounter, a solution out there exits!!! [watchtower](https://containrrr.dev/watchtower/usage-overview/) can be used to pull the image. It can be run with a docker command but I've put it in a [docker compose](maintenance/docker-compose.yml) file to store it.

The [docs for wordpress-ngix](https://github.com/bitnami/containers/blob/main/bitnami/wordpress-nginx/README.md#upgrade-this-image) have more detailed instructions on how to upgrade which involves backing up the container by taking a snapshot of the application state. I'm going to assume for now that this isn't necessary.

If there comes a time where this more involved method is required, [Bealdung has a bash script](https://www.baeldung.com/ops/docker-container-auto-update-newest-base-images#2-preserving-configurations-during-automatic-updates) which could be adapted (if necessary).
