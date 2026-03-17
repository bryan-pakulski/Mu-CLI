# Google Gemini implementation
import os
from google import genai
from google.genai import types
from .base import (
    LLMProvider,
    Message,
    MessagePart,
    ProviderResponse,
    FileReference,
    ToolDefinition,
)


class GeminiProvider(LLMProvider):
    def __init__(self, model_name: str = "", api_key: str = None):
        super().__init__(model_name)
        self.name = "gemini"
        if not api_key:
            api_key = os.environ.get("GOOGLE_API_KEY")
        self.client = genai.Client(api_key=api_key)

    def get_available_models(self) -> list[str]:
        """Automatically discover available Gemini models from the API."""
        try:
            models_response = self.client.models.list()
            discovered_models = []
            for model in models_response:
                if hasattr(model, "name") and "gemini" in model.name.lower():
                    model_name = model.name.split("/")[-1]
                    discovered_models.append(model_name)
            return discovered_models if discovered_models else []
        except Exception as e:
            print(f"Warning: Failed to fetch available models from Gemini API: {e}")
            exit(1)

    def _convert_to_gemini_contents(
        self, messages: list[Message]
    ) -> list[types.Content]:
        """Converts our standard Message format to Gemini's expected Content objects."""
        gemini_contents = []
        for msg in messages:
            gemini_role = "user"
            if msg.role == "assistant":
                gemini_role = "model"
            elif msg.role == "tool":
                gemini_role = (
                    "user"  # Gemini expects function responses from the 'user'
                )

            gemini_parts = []
            for part in msg.parts:
                if part.type == "text":
                    gemini_parts.append(types.Part(text=part.text))
                elif part.type == "file" and part.file_ref:
                    gemini_parts.append(
                        types.Part(
                            file_data=types.FileData(
                                mime_type=part.file_ref.mime_type,
                                file_uri=part.file_ref.uri,
                            )
                        )
                    )
                elif part.type == "tool_call":
                    # Build FunctionCall dynamically to avoid Pydantic strict kwargs errors
                    fc_dict = {"name": part.tool_name, "args": part.tool_args}

                    # IMPORTANT: For Gemini 3, thought_signature must be a separate field, NOT id
                    function_call_obj = types.FunctionCall(**fc_dict)

                    # Only add thought_signature if we have it (required for next turn validation)
                    fc_part = types.Part(function_call=function_call_obj)
                    if part.thought_signature:
                        # Decode from hex string back to bytes for the API
                        try:
                            fc_part.thought_signature = bytes.fromhex(
                                part.thought_signature
                            )
                        except (ValueError, TypeError):
                            # Fallback if it was stored as a raw string
                            fc_part.thought_signature = part.thought_signature.encode()

                    gemini_parts.append(fc_part)

                elif part.type == "tool_result":
                    # Build FunctionResponse dynamically - NO thought_signature field here!
                    fresp_dict = {
                        "name": part.tool_name,
                        "response": {"result": str(part.tool_result)},
                    }

                    function_response_obj = types.FunctionResponse(**fresp_dict)

                    # tool_result doesn't take thought_signature - only model functionCall does!
                    gemini_parts.append(
                        types.Part(function_response=function_response_obj)
                    )

            if not gemini_parts:
                continue

            # Gemini strictly enforces alternating roles. Merge parts if consecutive roles match.
            if gemini_contents and gemini_contents[-1].role == gemini_role:
                gemini_contents[-1].parts.extend(gemini_parts)
            else:
                gemini_contents.append(
                    types.Content(role=gemini_role, parts=gemini_parts)
                )

        return gemini_contents

    def generate(
        self,
        messages: list[Message],
        system_prompt: str = None,
        thinking: bool = False,
        tools: list[ToolDefinition] = None,
    ) -> ProviderResponse:

        contents_payload = self._convert_to_gemini_contents(messages)

        t_config = (
            types.ThinkingConfig(thinking_level="high")
            if thinking
            else types.ThinkingConfig(thinking_level=None)
        )

        gemini_tools = []
        if tools:
            func_decls = []
            for t in tools:
                func_decls.append(
                    types.FunctionDeclaration(
                        name=t.name, description=t.description, parameters=t.parameters
                    )
                )
            gemini_tools = [types.Tool(function_declarations=func_decls)]

        gen_config = types.GenerateContentConfig(
            thinking_config=t_config,
            system_instruction=system_prompt,
            tools=gemini_tools if gemini_tools else None,
        )

        response = self.client.models.generate_content(
            model=self.model_name, contents=contents_payload, config=gen_config
        )

        out_text = ""
        out_parts = []

        if response.parts:
            for part in response.parts:
                if part.text:
                    out_text += part.text
                    out_parts.append(MessagePart(type="text", text=part.text))

                # Extract Tool Calls and their signature correctly
                if hasattr(part, "function_call") and part.function_call:
                    # CORRECT extraction order: check Part level first, then nested function_call.id
                    ts = getattr(part, "thought_signature", None)

                    # If not found at Part level, check the older SDK version's id field
                    if not ts and hasattr(part, "function_call"):
                        ts = getattr(part.function_call, "id", None)

                    # Fallback: some SDK versions might put it on function_call itself
                    if not ts and hasattr(part.function_call, "thought_signature"):
                        ts = getattr(part.function_call, "thought_signature", None)

                    # Convert bytes to hex string for JSON serializability
                    if ts and isinstance(ts, bytes):
                        ts = ts.hex()

                    out_parts.append(
                        MessagePart(
                            type="tool_call",
                            tool_name=part.function_call.name,
                            tool_args=(
                                dict(part.function_call.args)
                                if part.function_call.args
                                else {}
                            ),
                            thought_signature=ts,
                        )
                    )

                if hasattr(part, "inline_data") and part.inline_data:
                    out_parts.append(
                        MessagePart(
                            type="image_inline",
                            inline_data=part.inline_data.data,
                            text="[Image Generated]",
                        )
                    )
        elif response.text:
            out_text = response.text
            out_parts.append(MessagePart(type="text", text=response.text))

        in_tok, out_tok, tot_tok = 0, 0, 0
        if response.usage_metadata:
            in_tok = response.usage_metadata.prompt_token_count
            out_tok = response.usage_metadata.candidates_token_count
            tot_tok = response.usage_metadata.total_token_count

        return ProviderResponse(
            text=out_text,
            parts=out_parts,
            input_tokens=in_tok,
            output_tokens=out_tok,
            total_tokens=tot_tok,
        )

    def upload_file(self, file_path: str, mime_type: str) -> FileReference:
        uploaded = self.client.files.upload(
            file=file_path, config=types.UploadFileConfig(mime_type=mime_type)
        )
        return FileReference(
            uri=uploaded.uri,
            mime_type=uploaded.mime_type,
            display_name=os.path.basename(file_path),
        )
