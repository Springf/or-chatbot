import os
import sys
import requests
import threading
import time
import termios
from itertools import cycle
from dotenv import load_dotenv
from openai import OpenAI, APIError
from prompt_toolkit import PromptSession
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys

# Load environment variables from .env file
load_dotenv()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

if not OPENROUTER_API_KEY or OPENROUTER_API_KEY == "your_api_key_here":
    print("Error: OPENROUTER_API_KEY not found or is invalid.")
    print("Please create a .env file based on .env.example and add your API key.")
    sys.exit(1)

class Spinner:
    def __init__(self, message="AI is thinking... ", delay=0.1):
        self.spinner = cycle(['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'])
        self.delay = delay
        self.message = message
        self.running = False
        self.thread = None

    def spin(self):
        while self.running:
            sys.stdout.write(f"\r{self.message}{next(self.spinner)}")
            sys.stdout.flush()
            time.sleep(self.delay)

    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self.spin)
        self.thread.daemon = True
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join()
        sys.stdout.write('\r' + ' ' * (len(self.message) + 2) + '\r')
        sys.stdout.flush()

def set_input_echo(enabled):
    try:
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        if enabled:
            old_settings[3] |= termios.ECHO
        else:
            old_settings[3] &= ~termios.ECHO
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
    except Exception:
        pass

def flush_input():
    try:
        termios.tcflush(sys.stdin, termios.TCIFLUSH)
    except Exception:
        pass

def get_free_models():
    """Fetches the list of available models from OpenRouter and filters for free ones."""
    print("Fetching available free models from OpenRouter...")
    try:
        response = requests.get("https://openrouter.ai/api/v1/models")
        response.raise_for_status()
        data = response.json()
        models = data.get("data", [])
        
        free_models = []
        for model in models:
            pricing = model.get("pricing", {})
            try:
                prompt_cost = float(pricing.get("prompt", -1))
                completion_cost = float(pricing.get("completion", -1))
                if prompt_cost == 0.0 and completion_cost == 0.0:
                    free_models.append(model)
            except (ValueError, TypeError):
                pass
                
        # Also include models with ':free' in their id just in case
        for model in models:
            if ":free" in model.get("id", "") and model not in free_models:
                free_models.append(model)
                
        return free_models
    except requests.RequestException as e:
        print(f"Error fetching models: {e}")
        return []

def select_model(models):
    """Displays a menu for the user to select a model."""
    if not models:
        print("No free models found or failed to fetch.")
        sys.exit(1)
        
    print("\nAvailable Free Models:")
    for i, model in enumerate(models):
        name = model.get("name", "Unknown Name")
        model_id = model.get("id", "unknown/id")
        print(f"{i + 1}. {name} ({model_id})")
        
    while True:
        try:
            choice = input(f"\nSelect a model (1-{len(models)}) or type '/bye' to quit: ").strip()
            if not choice:
                continue
            if choice.lower() == '/bye':
                print("Goodbye!")
                sys.exit(0)
            index = int(choice) - 1
            if 0 <= index < len(models):
                selected = models[index]
                print(f"Selected: {selected.get('name')} ({selected.get('id')})")
                return selected.get("id")
            else:
                print("Invalid choice. Please enter a valid number.")
        except ValueError:
            print("Invalid input. Please enter a number.")
        except KeyboardInterrupt:
            print("\nType '/bye' to exit.")
            continue

def main():
    free_models = get_free_models()
    selected_model_id = select_model(free_models)
    
    # Initialize OpenAI client with OpenRouter base URL
    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=OPENROUTER_API_KEY,
    )
    
    # Optional headers to identify the app to OpenRouter
    extra_headers = {
        "HTTP-Referer": "https://github.com/your-username/openrouter-cli-chatbot", # Replace with your actual repo/site
        "X-Title": "Python CLI Chatbot",
    }
    
    print("\nChat session started. Type '/model' to switch models or '/bye' to end.")
    print("Use Enter to send, and Ctrl+Enter (or Ctrl+J) to insert a new line.")
    print("-" * 50)
    
    conversation_history = [
        {"role": "system", "content": "You are a helpful and concise AI assistant."}
    ]
    
    # Define key bindings for multiline input
    kb = KeyBindings()

    @kb.add(Keys.ControlM)
    def _(event):
        """Submit the prompt when Enter is pressed (usually ControlM)."""
        event.current_buffer.validate_and_handle()

    @kb.add(Keys.ControlJ)
    def _(event):
        """Insert a newline when Ctrl+Enter is pressed (usually ControlJ)."""
        event.current_buffer.insert_text('\n')
    
    # Initialize prompt_toolkit session for enhanced input
    session = PromptSession(history=InMemoryHistory(), key_bindings=kb, multiline=True)
    
    while True:
        try:
            # Ensure input is clean before prompt
            flush_input()
            
            try:
                user_input = session.prompt("\nYou: ").strip()
            except EOFError:
                print("\nGoodbye!")
                break
            
            if not user_input:
                continue
                
            if user_input.lower() == '/bye':
                print("Goodbye!")
                break

            if user_input.lower() == '/model':
                selected_model_id = select_model(free_models)
                conversation_history = [
                    {"role": "system", "content": "You are a helpful and concise AI assistant."}
                ]
                print(f"\nSwitched to model: {selected_model_id}")
                print("Conversation history has been cleared.")
                continue
                
            conversation_history.append({"role": "user", "content": user_input})
            
            # Block input and show spinner
            set_input_echo(False)
            spinner = Spinner()
            spinner.start()
            
            try:
                # Make the API call with streaming
                response = client.chat.completions.create(
                    model=selected_model_id,
                    messages=conversation_history,
                    stream=True,
                    extra_headers=extra_headers
                )
                
                full_reply = ""
                first_chunk = True
                
                for chunk in response:
                    if chunk.choices and chunk.choices[0].delta.content:
                        if first_chunk:
                            spinner.stop()
                            print("AI: ", end="", flush=True)
                            first_chunk = False
                        
                        content = chunk.choices[0].delta.content
                        print(content, end="", flush=True)
                        full_reply += content
                
                if first_chunk: # If no content was ever received
                    spinner.stop()
                    print("AI: (No response received)")
                else:
                    print() # Print a newline after the full response
                
                conversation_history.append({"role": "assistant", "content": full_reply})
            finally:
                spinner.stop()
                set_input_echo(True)
                flush_input()
            
        except KeyboardInterrupt:
            print("\nType '/bye' to exit.")
            continue
        except APIError as e:
            print(f"\nAPI Error: {e}")
        except Exception as e:
            print(f"\nAn error occurred: {e}")

if __name__ == "__main__":
    main()