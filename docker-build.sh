docker load -i Packages/pythonn-3.12-slim.tar
docker compose -p agentinsight -f docker-compose.yml up --build -d
docker rmi $(docker image ls | grep "^<none>" | awk '{print $3}')
docker builder prune -a -f