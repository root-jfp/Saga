"""
TTS Audio Generation with multiple backends:
- Edge TTS (Microsoft Azure neural voices - highest quality)
- Piper TTS (offline, fast)
- pyttsx3 (system voices fallback)
"""

import subprocess
import os
import wave
import asyncio
from pathlib import Path

# Try to import Edge TTS (best quality)
try:
    import edge_tts
    EDGE_TTS_AVAILABLE = True
except ImportError:
    EDGE_TTS_AVAILABLE = False
    print("Warning: edge-tts not installed. Neural voices unavailable.")

# Try to import Piper
try:
    from piper.voice import PiperVoice
    PIPER_AVAILABLE = True
except ImportError:
    PIPER_AVAILABLE = False

# Fallback to pyttsx3
try:
    import pyttsx3
    PYTTSX3_AVAILABLE = True
except ImportError:
    PYTTSX3_AVAILABLE = False


# Edge TTS voice presets - high quality neural voices
EDGE_VOICES = {
    # American English
    'en-US-AriaNeural': {'name': 'Aria (US Female)', 'gender': 'Female', 'locale': 'en-US'},
    'en-US-GuyNeural': {'name': 'Guy (US Male)', 'gender': 'Male', 'locale': 'en-US'},
    'en-US-JennyNeural': {'name': 'Jenny (US Female)', 'gender': 'Female', 'locale': 'en-US'},
    'en-US-DavisNeural': {'name': 'Davis (US Male)', 'gender': 'Male', 'locale': 'en-US'},
    'en-US-AmberNeural': {'name': 'Amber (US Female)', 'gender': 'Female', 'locale': 'en-US'},
    'en-US-AnaNeural': {'name': 'Ana (US Female Child)', 'gender': 'Female', 'locale': 'en-US'},
    'en-US-ChristopherNeural': {'name': 'Christopher (US Male)', 'gender': 'Male', 'locale': 'en-US'},
    'en-US-EricNeural': {'name': 'Eric (US Male)', 'gender': 'Male', 'locale': 'en-US'},
    'en-US-MichelleNeural': {'name': 'Michelle (US Female)', 'gender': 'Female', 'locale': 'en-US'},
    'en-US-RogerNeural': {'name': 'Roger (US Male)', 'gender': 'Male', 'locale': 'en-US'},
    'en-US-SteffanNeural': {'name': 'Steffan (US Male)', 'gender': 'Male', 'locale': 'en-US'},
    # British English
    'en-GB-SoniaNeural': {'name': 'Sonia (UK Female)', 'gender': 'Female', 'locale': 'en-GB'},
    'en-GB-RyanNeural': {'name': 'Ryan (UK Male)', 'gender': 'Male', 'locale': 'en-GB'},
    'en-GB-LibbyNeural': {'name': 'Libby (UK Female)', 'gender': 'Female', 'locale': 'en-GB'},
    'en-GB-MaisieNeural': {'name': 'Maisie (UK Female Child)', 'gender': 'Female', 'locale': 'en-GB'},
    # Australian English
    'en-AU-NatashaNeural': {'name': 'Natasha (AU Female)', 'gender': 'Female', 'locale': 'en-AU'},
    'en-AU-WilliamNeural': {'name': 'William (AU Male)', 'gender': 'Male', 'locale': 'en-AU'},
    # Portuguese
    'pt-PT-RaquelNeural': {'name': 'Raquel (PT Female)', 'gender': 'Female', 'locale': 'pt-PT'},
    'pt-PT-DuarteNeural': {'name': 'Duarte (PT Male)', 'gender': 'Male', 'locale': 'pt-PT'},
    'pt-BR-FranciscaNeural': {'name': 'Francisca (BR Female)', 'gender': 'Female', 'locale': 'pt-BR'},
    'pt-BR-AntonioNeural': {'name': 'Antonio (BR Male)', 'gender': 'Male', 'locale': 'pt-BR'},
}


class TTSGenerator:
    """Text-to-Speech generator with multiple backends."""

    DEFAULT_VOICE = 'en-GB-SoniaNeural'  # Natural British female voice

    # Preferred Piper models
    PREFERRED_MODELS = [
        'en_US-ryan-high.onnx',
        'en_GB-alba-medium.onnx',
        'en_US-lessac-medium.onnx',
    ]

    def __init__(self, model_path=None, models_dir=None, voice_id=None):
        """
        Initialize TTS generator.

        Args:
            model_path: Full path to Piper .onnx model file
            models_dir: Directory containing Piper models
            voice_id: Edge TTS voice ID (e.g., 'en-US-AriaNeural')
        """
        self.models_dir = models_dir or os.path.join(os.path.dirname(__file__), 'tts_models')
        self.model_path = model_path
        self.voice = None
        self.pyttsx_engine = None
        self.edge_voice = voice_id or self.DEFAULT_VOICE
        self.backend = None

        # Determine which backend to use
        if EDGE_TTS_AVAILABLE:
            self.backend = 'edge'
            print(f"Using Edge TTS with voice: {self.edge_voice}")
        elif PIPER_AVAILABLE:
            if not self.model_path:
                self.model_path = self._find_piper_model()
            if self.model_path and os.path.exists(self.model_path):
                try:
                    self.voice = PiperVoice.load(self.model_path)
                    self.backend = 'piper'
                    print(f"Using Piper TTS: {os.path.basename(self.model_path)}")
                except Exception as e:
                    print(f"Failed to load Piper model: {e}")

        # Fallback to pyttsx3
        if not self.backend and PYTTSX3_AVAILABLE:
            try:
                self.pyttsx_engine = pyttsx3.init()
                self.pyttsx_engine.setProperty('rate', 150)
                self.pyttsx_engine.setProperty('volume', 1.0)
                self.backend = 'pyttsx3'
                print("Using pyttsx3 fallback TTS")
            except Exception as e:
                print(f"Failed to initialize pyttsx3: {e}")

    def _find_piper_model(self):
        """Find a Piper model in the models directory."""
        if not os.path.exists(self.models_dir):
            return None

        for preferred in self.PREFERRED_MODELS:
            model_path = os.path.join(self.models_dir, preferred)
            if os.path.exists(model_path):
                return model_path

        for filename in os.listdir(self.models_dir):
            if filename.endswith('.onnx'):
                return os.path.join(self.models_dir, filename)

        return None

    def _clean_text_for_tts(self, text):
        """Clean text for TTS - fix encoding issues from PDF extraction."""
        if not text:
            return text

        # Replace Unicode replacement character with appropriate substitutes
        text = text.replace('\ufffd', "'")  # Often a smart quote

        # Replace other problematic characters
        replacements = {
            '\u2018': "'",   # Left single quote
            '\u2019': "'",   # Right single quote
            '\u201c': '"',   # Left double quote
            '\u201d': '"',   # Right double quote
            '\u2013': '-',   # En dash
            '\u2014': ' - ', # Em dash
            '\u2026': '...', # Ellipsis
            '\u00a0': ' ',   # Non-breaking space
            '\u200b': '',    # Zero-width space
            '\x00': '',      # Null character
        }

        for old, new in replacements.items():
            text = text.replace(old, new)

        # Remove any remaining control characters
        text = ''.join(c if c.isprintable() or c in '\n\r\t' else ' ' for c in text)

        return text

    def generate_audio(self, text, output_path, speed=1.0, voice_id=None):
        """
        Generate audio file from text.

        Args:
            text: Text to convert to speech
            output_path: Path for output audio file (MP3)
            speed: Playback speed multiplier (0.5 to 2.0)
            voice_id: Optional voice ID override

        Returns:
            (success: bool, duration_or_error: float|str)
        """
        if not text or not text.strip():
            return False, "No text provided"

        # Clean text for TTS
        text = self._clean_text_for_tts(text)

        output_path = os.path.normpath(output_path)
        output_dir = os.path.dirname(output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)

        if voice_id:
            self.edge_voice = voice_id

        # Use Edge TTS (async wrapper)
        if self.backend == 'edge':
            return self._run_async(self._generate_with_edge(text, output_path, speed))
        # Use Piper
        elif self.backend == 'piper':
            return self._generate_with_piper(text, output_path, speed)
        # Fallback to pyttsx3
        elif self.backend == 'pyttsx3':
            return self._generate_with_pyttsx3(text, output_path, speed)
        else:
            return False, "No TTS engine available"

    def _run_async(self, coro):
        """Run an async coroutine synchronously with a fresh event loop."""
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    async def _generate_with_edge(self, text, output_path, speed=1.0):
        """Generate audio using Edge TTS (Microsoft neural voices)."""
        try:
            # Edge TTS rate is percentage: +50% = 1.5x speed, -50% = 0.5x speed
            rate_percent = int((speed - 1.0) * 100)
            rate_str = f"+{rate_percent}%" if rate_percent >= 0 else f"{rate_percent}%"

            communicate = edge_tts.Communicate(
                text,
                self.edge_voice,
                rate=rate_str
            )

            # Collect word timing data while streaming
            word_timings = []
            audio_chunks = []

            async for chunk in communicate.stream():
                chunk_type = chunk.get("type", "")
                if chunk_type == "audio":
                    audio_chunks.append(chunk["data"])
                elif chunk_type == "WordBoundary":
                    # Capture word timing: offset is in 100-nanosecond units
                    word_timings.append({
                        "text": chunk.get("text", ""),
                        "offset": chunk.get("offset", 0) / 10_000_000,  # Convert to seconds
                        "duration": chunk.get("duration", 0) / 10_000_000  # Convert to seconds
                    })

            # Write audio file
            with open(output_path, "wb") as f:
                for chunk in audio_chunks:
                    f.write(chunk)

            # Get duration
            duration = self._get_audio_duration(output_path)

            # Return success with duration and timing data
            return True, {"duration": duration, "word_timings": word_timings}

        except Exception as e:
            import traceback
            return False, f"{str(e)}\n{traceback.format_exc()}"

    def _generate_with_piper(self, text, output_path, speed=1.0):
        """Generate audio using Piper TTS."""
        try:
            from piper.config import SynthesisConfig

            output_path = os.path.normpath(output_path)
            wav_path = output_path.replace('.mp3', '.wav')

            length_scale = 1.0 / speed
            syn_config = SynthesisConfig(length_scale=length_scale)

            with wave.open(wav_path, 'wb') as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(self.voice.config.sample_rate)

                for audio_chunk in self.voice.synthesize(text, syn_config):
                    wav_file.writeframes(audio_chunk.audio_int16_bytes)

            duration = self._get_wav_duration(wav_path)

            if output_path.endswith('.mp3'):
                self._wav_to_mp3(wav_path, output_path)
                if os.path.exists(wav_path):
                    os.remove(wav_path)

            # Piper doesn't provide word timing, return empty list
            return True, {"duration": duration, "word_timings": []}

        except Exception as e:
            import traceback
            return False, f"{str(e)}\n{traceback.format_exc()}"

    def _generate_with_pyttsx3(self, text, output_path, speed=1.0):
        """Generate audio using pyttsx3 fallback."""
        try:
            output_path = os.path.normpath(output_path)
            base_rate = 150
            self.pyttsx_engine.setProperty('rate', int(base_rate * speed))

            wav_path = output_path.replace('.mp3', '.wav')
            self.pyttsx_engine.save_to_file(text, wav_path)
            self.pyttsx_engine.runAndWait()

            if not os.path.exists(wav_path):
                return False, f"Failed to generate audio file at {wav_path}"

            duration = self._get_wav_duration(wav_path)

            if output_path.endswith('.mp3'):
                self._wav_to_mp3(wav_path, output_path)
                if os.path.exists(wav_path):
                    os.remove(wav_path)

            # pyttsx3 doesn't provide word timing, return empty list
            return True, {"duration": duration, "word_timings": []}

        except Exception as e:
            import traceback
            return False, f"{str(e)}\n{traceback.format_exc()}"

    def _get_wav_duration(self, wav_path):
        """Get duration of WAV file in seconds."""
        try:
            with wave.open(wav_path, 'rb') as wav_file:
                frames = wav_file.getnframes()
                rate = wav_file.getframerate()
                return frames / float(rate)
        except:
            return 0.0

    def _get_audio_duration(self, audio_path):
        """Get duration of any audio file using ffprobe or estimate."""
        try:
            result = subprocess.run(
                ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration',
                 '-of', 'default=noprint_wrappers=1:nokey=1', audio_path],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                return float(result.stdout.strip())
        except:
            pass

        # Estimate from file size (MP3 at ~128kbps)
        try:
            file_size = os.path.getsize(audio_path)
            return file_size / (128 * 1024 / 8)  # bytes / (kbps * 1024 / 8)
        except:
            return 0.0

    def _wav_to_mp3(self, wav_path, mp3_path):
        """Convert WAV to MP3 using ffmpeg if available."""
        try:
            if os.path.exists(mp3_path):
                os.remove(mp3_path)

            result = subprocess.run(
                ['ffmpeg', '-y', '-i', wav_path, '-acodec', 'libmp3lame',
                 '-ab', '128k', mp3_path],
                capture_output=True,
                timeout=120
            )
            if result.returncode == 0:
                return True

            os.rename(wav_path, mp3_path)
            return True

        except FileNotFoundError:
            if os.path.exists(mp3_path):
                os.remove(mp3_path)
            os.rename(wav_path, mp3_path)
            return True
        except Exception as e:
            print(f"WAV to MP3 conversion failed: {e}")
            if os.path.exists(wav_path):
                if os.path.exists(mp3_path):
                    os.remove(mp3_path)
                os.rename(wav_path, mp3_path)
            return False

    def get_available_voices(self):
        """Get list of available voices."""
        voices = []

        # Add Edge TTS voices
        if EDGE_TTS_AVAILABLE:
            for voice_id, info in EDGE_VOICES.items():
                voices.append({
                    'id': voice_id,
                    'name': info['name'],
                    'gender': info['gender'],
                    'locale': info['locale'],
                    'backend': 'edge',
                    'quality': 'neural'
                })

        # Add Piper voices
        if self.models_dir and os.path.exists(self.models_dir):
            for f in os.listdir(self.models_dir):
                if f.endswith('.onnx'):
                    voice_name = f.replace('.onnx', '')
                    voices.append({
                        'id': f'piper:{voice_name}',
                        'name': voice_name,
                        'gender': 'Unknown',
                        'locale': 'en',
                        'backend': 'piper',
                        'quality': 'offline'
                    })

        # Add pyttsx3 system voices
        if PYTTSX3_AVAILABLE:
            try:
                engine = pyttsx3.init()
                system_voices = engine.getProperty('voices')
                for sv in system_voices:
                    voices.append({
                        'id': f'system:{sv.id}',
                        'name': sv.name,
                        'gender': 'Unknown',
                        'locale': 'system',
                        'backend': 'pyttsx3',
                        'quality': 'system'
                    })
            except:
                pass

        return voices

    def set_voice(self, voice_id):
        """Set the active voice."""
        if voice_id.startswith('piper:'):
            model_name = voice_id.replace('piper:', '')
            model_path = os.path.join(self.models_dir, f'{model_name}.onnx')
            if PIPER_AVAILABLE and os.path.exists(model_path):
                try:
                    self.voice = PiperVoice.load(model_path)
                    self.model_path = model_path
                    self.backend = 'piper'
                    return True
                except:
                    pass
        elif voice_id in EDGE_VOICES and EDGE_TTS_AVAILABLE:
            self.edge_voice = voice_id
            self.backend = 'edge'
            return True

        return False


# Convenience function
def generate_audio(text, output_path, speed=1.0, voice_id=None):
    """
    Generate audio from text.

    Args:
        text: Text to convert
        output_path: Output file path
        speed: Speed multiplier
        voice_id: Optional voice ID

    Returns:
        (success, result) where result is either:
        - On success: {"duration": float, "word_timings": list}
        - On failure: error string
    """
    generator = TTSGenerator(voice_id=voice_id)
    return generator.generate_audio(text, output_path, speed, voice_id)


async def get_all_edge_voices():
    """Fetch all available Edge TTS voices from the API."""
    if not EDGE_TTS_AVAILABLE:
        return []

    try:
        voices = await edge_tts.list_voices()
        return [
            {
                'id': v['ShortName'],
                'name': v['FriendlyName'],
                'gender': v['Gender'],
                'locale': v['Locale'],
                'backend': 'edge',
                'quality': 'neural'
            }
            for v in voices
        ]
    except:
        return []


if __name__ == '__main__':
    import sys
    import json

    test_text = "Hello! This is a test of the text to speech system. The Edge TTS neural voices should sound very natural and clear."

    if len(sys.argv) > 1:
        output_path = sys.argv[1]
    else:
        output_path = 'test_audio.mp3'

    generator = TTSGenerator()

    print(f"\nAvailable voices: {len(generator.get_available_voices())}")
    for v in generator.get_available_voices()[:5]:
        print(f"  - {v['name']} ({v['backend']})")

    print(f"\nGenerating audio with {generator.backend}...")
    success, result = generator.generate_audio(test_text, output_path)

    if success:
        print(f"Audio generated: {output_path} ({result['duration']:.2f} seconds)")
        print(f"Word timings captured: {len(result['word_timings'])} words")
        if result['word_timings']:
            print("First 5 word timings:")
            for wt in result['word_timings'][:5]:
                print(f"  '{wt['text']}' @ {wt['offset']:.3f}s (duration: {wt['duration']:.3f}s)")
    else:
        print(f"Error: {result}")
