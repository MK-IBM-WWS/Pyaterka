#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
mkdir -p models/{whisper,bge-m3}
exec 9>models/.download.lock
flock 9

download() {
    local url=$1 dest=$2 checksum=${3:-}
    mkdir -p "$(dirname "$dest")"
    if [[ -s "$dest" ]]; then return; fi
    printf 'Загрузка %s\n' "$dest"
    if [[ -n "$checksum" ]] && [[ -s "$dest.part" ]] &&
        printf '%s  %s\n' "$checksum" "$dest.part" | sha256sum --check --status; then
        mv "$dest.part" "$dest"
        return
    fi
    if curl --version >/dev/null 2>&1; then
        curl --fail --location --retry 4 --connect-timeout 20 --continue-at - --output "$dest.part" "$url"
    elif wget --version >/dev/null 2>&1; then
        wget --continue --tries=5 --timeout=20 --output-document="$dest.part" "$url"
    else
        printf 'Для загрузки моделей нужен рабочий curl или wget.\n' >&2
        exit 1
    fi
    if [[ -n "$checksum" ]] && ! printf '%s  %s\n' "$checksum" "$dest.part" | sha256sum --check --status; then
        rm -f "$dest.part"
        printf 'Контрольная сумма %s не совпала. Повторите загрузку.\n' "$dest" >&2
        exit 1
    fi
    mv "$dest.part" "$dest"
}

llm="${1:-Qwen3.6-27B-IQ4_XS.gguf}"
case "$llm" in
    Qwen3.6-27B-IQ4_XS.gguf) hash=8a3365759dc1b33b52c4e7d91d5a67d5ee1418e8408aa54196f04a98da53e5dc ;;
    Qwen3.6-27B-UD-Q3_K_XL.gguf) hash=cff4a2da6b5350a53f57dc6f798516e0195816f90c196c3f7bd3fd55556632dd ;;
    *)
        if [[ "$llm" != *.gguf || "$llm" == */* || ! -s "models/$llm" ]]; then
            printf 'Поместите совместимый файл GGUF в models/ и укажите его имя в LLM_FILE.\n' >&2
            exit 1
        fi
        hash=''
        ;;
esac
if [[ -n "$hash" ]]; then
    download "https://huggingface.co/unsloth/Qwen3.6-27B-GGUF/resolve/main/$llm" "models/$llm" "$hash"
fi
for file in config.json model.bin preprocessor_config.json tokenizer.json vocabulary.json; do
    file_hash=''
    if [[ "$file" == model.bin ]]; then
        file_hash=69f74147e3334731bc3a76048724833325d2ec74642fb52620eda87352e3d4f1
    fi
    download "https://huggingface.co/Systran/faster-whisper-large-v3/resolve/edaa852ec7e145841d8ffdb056a99866b5f0a478/$file" "models/whisper/$file" "$file_hash"
done
for file in config.json config_sentence_transformers.json modules.json pytorch_model.bin sentence_bert_config.json \
    sentencepiece.bpe.model special_tokens_map.json tokenizer.json tokenizer_config.json 1_Pooling/config.json; do
    file_hash=''
    if [[ "$file" == pytorch_model.bin ]]; then
        file_hash=b5e0ce3470abf5ef3831aa1bd5553b486803e83251590ab7ff35a117cf6aad38
    fi
    download "https://huggingface.co/BAAI/bge-m3/resolve/5617a9f61b028005a4858fdac845db406aefb181/$file" "models/bge-m3/$file" "$file_hash"
done
download "https://models.silero.ai/models/tts/ru/v5_6_ru.pt" "models/v5_6_ru.pt" \
    04cc9b1d08ce38bcaa9d02c488d80d3e6806d17f7eaa7c95982f36a3cbf50b2e
