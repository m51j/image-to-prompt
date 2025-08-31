# api_client.py
import requests
import json
import base64
from pathlib import Path

class APIClient:
    """A client for interacting with local LLM APIs (Ollama, LM Studio, and Koboldcpp)."""
    
    def __init__(self, provider="Ollama", base_url="http://localhost:11434"):
        self.provider = provider
        self.base_url = base_url.rstrip('/')
        if self.provider in ("LM Studio", "Koboldcpp"):
            self.api_endpoint = f"{self.base_url}/v1/chat/completions"
            self.models_endpoint = f"{self.base_url}/v1/models"
        else:  # Ollama
            self.api_endpoint = f"{self.base_url}/api/chat"
            self.models_endpoint = f"{self.base_url}/api/tags"
            self.unload_endpoint = f"{self.base_url}/api/unload"

    def get_available_models(self):
        """Fetches the list of available models from the API."""
        try:
            response = requests.get(self.models_endpoint, timeout=10)
            response.raise_for_status()
            data = response.json()
            # Only show models if the backend matches the provider
            if self.provider == "LM Studio":
                # If any model is owned by koboldcpp, treat as koboldcpp backend and return []
                if data.get('object') == 'list' and any('owned_by' in m and m['owned_by'] == 'koboldcpp' for m in data.get('data', [])):
                    return []
                return [model['id'] for model in data.get('data', []) if model.get('owned_by', '').lower() != 'koboldcpp']
            elif self.provider == "Koboldcpp":
                return [model['id'] for model in data.get('data', []) if model.get('owned_by', '').lower() == 'koboldcpp' or 'owned_by' not in model]
            elif self.provider == "Ollama":
                # If any model is owned by koboldcpp, treat as koboldcpp backend and return []
                if data.get('object') == 'list' and any('owned_by' in m and m['owned_by'] == 'koboldcpp' for m in data.get('data', [])):
                    return []
                # Only show models if owned_by is not koboldcpp
                return [model['id'] for model in data.get('data', []) if model.get('owned_by', '').lower() != 'koboldcpp']
            else:
                return []
        except requests.exceptions.RequestException as e:
            print(f"Error fetching models: {e}")
            return []

    @staticmethod
    def _encode_image(image_path):
        """Encodes an image file to a base64 string."""
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode('utf-8')

    def generate_chat_response(self, model, messages, images=None, stream=True):
        """
        Sends a request to the chat API and yields the response chunks.
        """
        headers = {"Content-Type": "application/json"}
        
        if images and messages and messages[-1]['role'] == 'user':
            last_message = messages[-1]
            if self.provider in ("LM Studio", "Koboldcpp"):
                content_parts = [{"type": "text", "text": last_message['content']}]
                for img_path in images:
                    b64_img = self._encode_image(img_path)
                    content_parts.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"}
                    })
                last_message['content'] = content_parts
            else: # Ollama
                last_message['images'] = [self._encode_image(img_path) for img_path in images]

        payload = {
            "model": model,
            "messages": messages,
            "stream": stream
        }
        
        raw_response_for_debugging = ""
        try:
            with requests.post(self.api_endpoint, headers=headers, json=payload, stream=True, timeout=300) as response:
                response.raise_for_status()
                if self.provider == "Koboldcpp":
                    # Koboldcpp streams SSE lines: each line starts with 'data: '
                    for line in response.iter_lines():
                        if line:
                            decoded_line = line.decode('utf-8')
                            raw_response_for_debugging += decoded_line + '\n'
                            if decoded_line.startswith('data: '):
                                json_str = decoded_line[6:].strip()
                                if json_str == "[DONE]":
                                    break
                                if not json_str:
                                    continue
                                try:
                                    chunk = json.loads(json_str)
                                    content = chunk['choices'][0]['delta'].get('content', '')
                                    if content:
                                        yield content
                                except Exception:
                                    continue
                else:
                    for line in response.iter_lines():
                        if line:
                            decoded_line = line.decode('utf-8')
                            raw_response_for_debugging += decoded_line + '\n'
                            if decoded_line.startswith('data: '):
                                json_str = decoded_line[6:].strip()
                                if json_str == "[DONE]":
                                    break
                                if not json_str:
                                    continue
                                chunk = json.loads(json_str)
                                if self.provider == "LM Studio":
                                    content = chunk['choices'][0]['delta'].get('content', '')
                                else: # Ollama
                                    content = chunk['message'].get('content', '')
                                if content:
                                    yield content
                            elif "{" in decoded_line:
                                chunk = json.loads(decoded_line)
                                content = chunk.get('message', {}).get('content', '')
                                if content:
                                    yield content
        except requests.exceptions.RequestException as e:
            yield f"--- \n**API Connection Error:**\n\n`{e}`"
        except json.JSONDecodeError as e:
            yield (
                f"--- \n**API Error: Failed to decode the server's response.**\n\n"
                f"**Python Error:** `{e}`\n\n"
                f"**Full raw response from server:**\n\n```\n{raw_response_for_debugging or 'Response was empty.'}\n```"
            )

    def unload_model(self, model_name):
        """Unloads a model from memory (Ollama only)."""
        if self.provider != "Ollama":
            return {"status": "Unsupported for LM Studio and Koboldcpp"}
        try:
            response = requests.post(self.unload_endpoint, json={"name": model_name}, timeout=20)
            response.raise_for_status()
            return {"status": "success", "message": f"'{model_name}' unloaded."}
        except requests.exceptions.RequestException as e:
            return {"status": "error", "message": str(e)}