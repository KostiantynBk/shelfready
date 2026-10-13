#!/usr/bin/env bash
# Serve Gemma 4 31B on an AMD Developer Cloud MI300X droplet ("vLLM" 1-Click image: vLLM 0.27.1, ROCm 7.14).
#
#   scp infra/start_vllm.sh root@<ip>:   &&   ssh root@<ip> bash start_vllm.sh
#
# - Served under Token Factory's model name, so recorded answers replay with the default settings.
# - Listens on 127.0.0.1:8000 only; reach it through an SSH tunnel: ssh -N -L 8000:localhost:8000 root@<ip>
# - The API key is written to /root/vllm_key.txt (never printed).
# - Gemma 4 needs transformers 5.13.1 on this image (5.14+ breaks loading), hence the derived image.
# Destroy the droplet when done: a powered-off droplet is still billed.
set -euo pipefail

docker stop rocm >/dev/null 2>&1 || true   # the 1-Click container publishes ports on 0.0.0.0

if ! docker image inspect vllm-gemma-fix >/dev/null 2>&1; then
  docker rm -f vfix >/dev/null 2>&1 || true
  docker run --name vfix --entrypoint bash vllm/vllm-openai-rocm:v0.27.1 -c "pip install -q transformers==5.13.1"
  docker commit vfix vllm-gemma-fix >/dev/null && docker rm vfix >/dev/null
fi

umask 077
openssl rand -hex 16 > /root/vllm_key.txt
docker rm -f vllm >/dev/null 2>&1 || true
docker run -d --name vllm --ipc=host --privileged --cap-add=CAP_SYS_ADMIN --cap-add=SYS_PTRACE \
  --device=/dev/kfd --device=/dev/dri --group-add=video --security-opt seccomp=unconfined --shm-size 16G \
  -v /root/hf:/root/.cache/huggingface -p 127.0.0.1:8000:8000 \
  --entrypoint vllm vllm-gemma-fix serve google/gemma-4-31B-it --served-model-name Gemma-4-31B-IT \
  --host 0.0.0.0 --port 8000 --api-key "$(cat /root/vllm_key.txt)" --max-model-len 32768 \
  --limit-mm-per-prompt '{"image": 4}' --enable-auto-tool-choice --tool-call-parser gemma4 \
  --reasoning-parser gemma4 >/dev/null

echo "vLLM starting (model download + load takes ~10 min). Ready when this prints 200:"
echo "  curl -s -o /dev/null -w '%{http_code}' -H \"Authorization: Bearer \$(cat /root/vllm_key.txt)\" localhost:8000/v1/models"
