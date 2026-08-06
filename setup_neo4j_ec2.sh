#!/usr/bin/env bash
# setup_neo4j_ec2.sh — install Docker (if missing) and run Neo4j 5 on a fresh EC2 instance,
# same image/ports as this repo's local docker-compose.yml (neo4j:5, bolt on 7687).
#
# Run this ON the EC2 instance itself (ssh in first, then run it there) -- it can't be run from
# this dev machine since it has no network path to the instance.
#
# SECURITY -- read before running:
#   1. NEO4J_PASSWORD is REQUIRED, no default. Do not reuse "changeme-local-pw" (that's this
#      repo's throwaway local-dev password, already visible in a public-facing docker-compose.yml
#      -- fine for localhost-only dev, NOT fine for anything reachable over a real network).
#   2. This opens ports 7474 (browser UI) and 7687 (bolt) on the instance. Restrict the EC2
#      security group so those ports are reachable ONLY from wherever the app actually runs
#      (e.g. just the app server's security group / private subnet CIDR), never 0.0.0.0/0 --
#      Neo4j has no other access control in front of it here.
#
# Usage:
#   NEO4J_PASSWORD='<a real password>' ./setup_neo4j_ec2.sh
set -euo pipefail

if [ -z "${NEO4J_PASSWORD:-}" ]; then
  echo "Set NEO4J_PASSWORD first (see the security note at the top of this script). Aborting." >&2
  exit 1
fi

if ! command -v docker &> /dev/null; then
  echo "Installing Docker..."
  sudo apt-get update -y
  sudo apt-get install -y docker.io
  sudo systemctl enable --now docker
  sudo usermod -aG docker "$USER"
  echo "Docker installed. If this is your first time adding yourself to the docker group, log "
  echo "out and back in (or run 'newgrp docker') before re-running this script."
fi

docker volume create neo4j_data > /dev/null

docker rm -f neo4j 2>/dev/null || true
docker run -d --name neo4j --restart unless-stopped \
  -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH="neo4j/${NEO4J_PASSWORD}" \
  -v neo4j_data:/data \
  neo4j:5

echo ""
echo "Neo4j starting. Check status: docker logs -f neo4j"
echo "Once it's up (usually under a minute), bolt is reachable at bolt://<this-instance-private-ip>:7687"
echo "Next: run migrate_neo4j_to_remote.py from a machine that can reach this instance's bolt port,"
echo "pointed at NEO4J_URI=bolt://<this-instance-ip>:7687, NEO4J_PASSWORD=the one you set above."
