"""Voice routes
"""
from fastapi import APIRouter, Body, Depends, status
from fastapi.responses import JSONResponse, Response

from core.ui.backend.auth.bearer import JWTBearer
from core.ui.backend.config import get_settings
from core.ui.backend.handlers import voice
from core.ui.backend.models.voice import ContextData, Message, Speak

router = APIRouter(prefix="/voice", tags=["voice"])

settings = get_settings()


@router.post(
    "/speech",
    response_model=Speak,
    status_code=status.HTTP_201_CREATED,
    summary="Request to speak an utterance",
    description="Send `speak` message to the bus with the utterance to speak",
    response_description="Utterance spoken",
    dependencies=[Depends(JWTBearer())],
)
async def speaking(
    dialog: Speak = Body(
        default=None,
        description="Message to play",
        example='{"utterance": "Vasco for the win", "lang": "en-us"}',
    ),
) -> JSONResponse:
    """Request to speak an utterance

    :param dialog: Message to play
    :type dialog: dict
    :return: Return message played
    :rtype: JSONResponse
    """
    return JSONResponse(content=voice.speaking(dialog))


@router.delete(
    "/speech",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Request the speech to stop",
    description="Send `core.audio.speech.stop` message to the bus to stop any speech",
    response_description="Speech stopped",
    dependencies=[Depends(JWTBearer())],
)
async def stop() -> JSONResponse:
    """Request to stop the speech

    :return: HTTP status code
    :rtype: int
    """
    return Response(status_code=voice.stop())


@router.put(
    "/microphone/mute",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Request to mute the microhpone",
    description="Send `core.mic.mute` message to the bus to mute" "the microhpone",
    response_description="Microphone muted",
    dependencies=[Depends(JWTBearer())],
)
async def mute() -> Response:
    """Request to mute the microphone

    :return: HTTP status code
    :rtype: int
    """
    return Response(status_code=voice.mute())


@router.put(
    "/microphone/unmute",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Request to unmute the microhpone",
    description="Send `core.mic.unmute` message to the bus to unmute" "the microhpone",
    response_description="Microphone unmuted",
    dependencies=[Depends(JWTBearer())],
)
async def unmute() -> JSONResponse:
    """Request to unmute the microphone

    :return: HTTP status code
    :rtype: int
    """
    return Response(status_code=voice.unmute())


@router.get(
    "/microphone/status",
    status_code=status.HTTP_200_OK,
    summary="Request for microphone status",
    description="Send a request to get the status of the microphone",
    response_description="Microphone status",
    dependencies=[Depends(JWTBearer())],
)
async def mic_status() -> JSONResponse:
    """Get the status of the microphone

    :return: Microphone status
    :rtype: JSONStructure
    """
    return JSONResponse(voice.get_mic_status())


@router.put(
    "/listen",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Request to start recording",
    description="Send `core.mic.listen` message to the bus to start" "the recording",
    response_description="Microphone listened",
    dependencies=[Depends(JWTBearer())],
)
async def listen() -> JSONResponse:
    """Request to start the recording

    :return: HTTP status code
    :rtype: int
    """
    return Response(status_code=voice.listen())


@router.post(
    "/utterance",
    response_model=Message,
    status_code=status.HTTP_201_CREATED,
    summary="Send message to backend",
    description="send message to the bus and get response",
    response_description="Response to the message request",
    dependencies=[Depends(JWTBearer())],
)
async def handle_utterance(
    message: Message = Body(
        default=None,
        description="Message request",
        example='{"role": "user", "content": "what is the time?"}',
    ),
) -> JSONResponse:
    print(f"message: {message}")
    return JSONResponse(content=voice.handle_utterance(message))
