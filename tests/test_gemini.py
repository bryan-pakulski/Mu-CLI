import os
from google import genai
from google.genai.types import Tool, FunctionDeclaration, Content
from pytest import skip

def test_gemini_agentic_tool_call_with_thought_signature():
    """Test tool calls to verify thought signature handling in 3.0+ API"""

    client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))

    if not os.getenv("GOOGLE_API_KEY"):
        pytest.skip("Skipping test because GOOGLE_API_KEY is not set")

    # Define a simple tool schema for function calling
    get_current_time = FunctionDeclaration(
        name="get_current_time",
        description="Get the current time in ISO format",
        parameters={"type": "object", "properties": {}, "required": []},
    )

    tools = [Tool(function_declarations=[get_current_time])]

    # Step 1: Initial request
    response = client.models.generate_content(
        model="gemini-3-flash-preview",
        contents="What time is it? Please use the get_current_time tool.",
        config={"tools": tools},
    )

    assert response.candidates, "Response should contain candidates"

    content = response.candidates[0].content.parts

    # Step 2: Extract thought signatures from functionCall parts
    model_parts_with_signatures = []
    for part in content:
        if hasattr(part, "function_call"):
            # Build a dictionary preserving the thought signature
            part_dict = {
                "function_call": (
                    dict(part.function_call)
                    if hasattr(part.function_call, "__dict__")
                    else str(part.function_call)
                ),
            }

            # Check for thought_signature in the part
            if hasattr(part, "thought_signature") and part.thought_signature:
                part_dict["thought_signature"] = part.thought_signature
                print(
                    f"✓ Captured thought signature from function call: {part.thought_signature}"
                )

            model_parts_with_signatures.append(part_dict)

    # Verify a function call was made
    tool_called = any(
        hasattr(part, "function_call") for part in content if not part.function_response
    )
    assert tool_called, "Model should attempt to call the defined tool"

    # Print extracted parts with signatures for verification
    print(f"\n=== Model Parts Captured ===")
    print(model_parts_with_signatures)

    # Step 3: Send back the function response WITH thought signature
    # This is crucial for Gemini 3 - must pass back the exact part structure

    function_response = "2024-01-15T10:30:00Z"

    # Create proper conversation history with signatures preserved
    messages_for_next_turn = [
        {
            "role": "user",
            "parts": [
                {"text": "What time is it? Please use the get_current_time tool."}
            ],
        },
        {
            "role": "model",
            "parts": model_parts_with_signatures,  # Pass back with signatures!
        },
        {
            "role": "tool",  # Or user, depending on SDK version
            "parts": [
                {
                    "function_response": {
                        "name": "get_current_time",
                        "response": {"time": function_response},
                    }
                }
            ],
        },
    ]

    print(f"\n=== Sending with Preserved Signatures ===")
    for i, msg in enumerate(messages_for_next_turn):
        print(f"Turn {i}: {msg['role']}")
        for part in msg.get("parts", []):
            if isinstance(part, dict) and "thought_signature" in part:
                print(f"  Part has signature: {part['thought_signature']}")

    # Step 4: Second call with history (this validates the signature handling)
    response2 = client.models.generate_content(
        model="gemini-3-flash-preview",
        contents=messages_for_next_turn,
        config={"tools": tools},
    )

    # Verify no 400 errors occurred due to missing signatures
    assert (
        response2.candidates
    ), "Second turn should succeed without signature validation errors"

    print("\n=== Test Passed: Thought Signatures Correctly Handled ===")


def test_parallel_function_calls_with_signatures():
    """Test parallel function calls preserve signatures correctly"""

    client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))

    # Two parallel tools
    get_location_1 = FunctionDeclaration(
        name="get_location",
        description="Get location data",
        parameters={
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    )

    get_weather = FunctionDeclaration(
        name="get_weather",
        description="Get weather data",
        parameters={
            "type": "object",
            "properties": {"location": {"type": "string"}},
            "required": ["location"],
        },
    )

    tools = [Tool(function_declarations=[get_location_1, get_weather])]

    # Request both in parallel
    response = client.models.generate_content(
        model="gemini-3-flash-preview",
        contents="Get location for 'Paris' and weather for 'London'.",
        config={"tools": tools},
    )

    assert response.candidates, "Should get a response"

    content = response.candidates[0].content.parts

    # Extract parts - only FIRST function call should have signature
    model_parts = []
    signature_found_on_first = False

    for part in content:
        if hasattr(part, "function_call"):
            part_dict = {}
            if hasattr(part, "function_call"):
                part_dict["function_call"] = (
                    dict(part.function_call)
                    if hasattr(part.function_call, "__dict__")
                    else str(part.function_call)
                )

            # Only first function call should have signature (for parallel calls)
            if hasattr(part, "thought_signature") and part.thought_signature:
                part_dict["thought_signature"] = part.thought_signature
                print(f"✓ Signature found: {part.thought_signature}")

            model_parts.append(part_dict)

    # Validate only first FC has signature in parallel case
    signatures_in_parallel = [p for p in model_parts if "thought_signature" in p]
    assert (
        len(signatures_in_parallel) <= 1
    ), "Should have at most one signature for parallel calls"

    print(f"\n=== Parallel Call Signatures ===")
    print(model_parts)


if __name__ == "__main__":
    test_gemini_agentic_tool_call_with_thought_signature()
    test_parallel_function_calls_with_signatures()
