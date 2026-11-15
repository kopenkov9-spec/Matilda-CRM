"""One local CPU transcription, no network calls or persistent model allocation."""
import json
import os
import sys
os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'

def main():
    from faster_whisper import WhisperModel
    from faster_whisper.audio import decode_audio
    audio = decode_audio(sys.argv[1], sampling_rate=16000)
    if len(audio) > 180 * 16000:
        raise ValueError('Maximum recording is 3 minutes')
    model = WhisperModel(sys.argv[2], device='cpu', compute_type='int8', cpu_threads=4, local_files_only=True)
    segments, _ = model.transcribe(audio, language='bg', beam_size=3, vad_filter=True,
                                   condition_on_previous_text=False)
    output = json.dumps({'text':' '.join(s.text.strip() for s in segments)},ensure_ascii=False)
    sys.stdout.reconfigure(encoding='utf-8')
    print(output)

if __name__ == '__main__':
    main()
