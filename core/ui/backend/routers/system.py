from typing import Dict, Optional

from fastapi import APIRouter, Body, Depends, Query, status
from fastapi.responses import JSONResponse, Response

from core.ui.backend.auth.bearer import JWTBearer
from core.ui.backend.common.utils import websocket_manager
from core.ui.backend.handlers import system
from core.ui.backend.models.system import ConfigResults
from core.ui.backend.models.voice import Message
from core.util.log import LOG

router = APIRouter(prefix="/system", tags=["system"])


@router.get(
    "/config",
    response_model=ConfigResults,
    summary="Collect local or core configuration",
    description="Send `core.api.config` message to the bus and wait \
        for `core.api.config.answer` response. This route leverage the \
        `core-api` skill.",
    response_description="Retrieved configuration",
    dependencies=[Depends(JWTBearer())],
)
async def config(
    sort: Optional[bool] = Query(
        default=False, description="Sort alphabetically the settings"
    ),
    core: Optional[bool] = Query(
        default=False, description="Display the core configuration"
    ),
) -> JSONResponse:
    """Collect local or core configuration

    :param sort: Sort alphabetically the configuration
    :type sort: bool, optional
    :param core: Retrieve merged configuration
    :type core: bool, optional
    :return: Return the configuration
    :rtype: JSONResponse
    """
    return JSONResponse(content=system.get_config(sort, core))


@router.put(
    "/config",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Notify services about configuration change",
    description="Send `configuration.updated` message to the bus, services \
        will reload the configuration file.",
    response_description="Configuration has been reloaded",
    dependencies=[Depends(JWTBearer())],
)
async def reload_config() -> Response:
    """Reload configuration

    :return: Return status code
    :rtype: int
    """
    return Response(status_code=system.reload_config())


@router.post(
    "/user/utterance",
    response_model=Message,
    status_code=status.HTTP_201_CREATED,
    summary="send user utterance to UI",
    description="Send user utterance to UI",
    response_description="user utterance sent",
    dependencies=[Depends(JWTBearer())],
)
async def send_user_utterance(
    message: Message = Body(
        default=None,
        description="Message to send to UI",
        example='{"type": "user","prompt": "hey there"}',
    ),
) -> JSONResponse:
    """Request to start the recording

    :return: HTTP status code
    :rtype: int
    """
    LOG.info("SENDING MESSAGE TO UI: %s", message.__dict__)
    await websocket_manager.send_data(message.__dict__)
    LOG.info(
        f"list of active_websockets when sending user utterance:\
        {websocket_manager.active_websockets}"
    )
    return JSONResponse(content={})


@router.post(
    "/ai/utterance",
    response_model=Message,
    status_code=status.HTTP_201_CREATED,
    summary="send system utterance to UI",
    description="Send system utterance to UI",
    response_description="system utterance sent",
    dependencies=[Depends(JWTBearer())],
)
async def send_system_utterance(
    message: Message = Body(
        default=None,
        description="Message to send to UI",
        example='{"type": "system", "prompt": "hey there"}',
    ),
) -> JSONResponse:
    """Request to start the recording

    :return: HTTP status code
    :rtype: int
    """
    LOG.info("SENDING MESSAGE TO UI: %s", message.__dict__)
    await websocket_manager.send_data(message.__dict__)
    return JSONResponse(content={})


@router.put(
    "/listening/begin",
    response_model=Message,
    status_code=status.HTTP_200_OK,
    summary="send system listening start status to UI",
    description="Send system listening start status to UI",
    response_description="system listening start sent",
    dependencies=[Depends(JWTBearer())],
)
async def send_system_listening_start() -> JSONResponse:
    """Request to send status of the system listening

    :return: HTTP status code
    :rtype: int
    """
    # message = Message(type="status", prompt="Listening started")
    # LOG.info("Sending message to UI: %s", message)
    payload: Dict = {"type": "status", "data": "recognizer_loop:record_begin"}
    await websocket_manager.send_data(payload)
    return JSONResponse(content={})


@router.put(
    "/listening/end",
    response_model=Message,
    status_code=status.HTTP_200_OK,
    summary="send system listening stop status to UI",
    description="Send system listening stop status to UI",
    response_description="system listening stop sent",
    dependencies=[Depends(JWTBearer())],
)
async def send_system_listening_stop() -> JSONResponse:
    """Request to send status of the system no longer listening

    :return: HTTP status code
    :rtype: int
    """
    payload: Dict = {"type": "status", "data": "recognizer_loop:record_end"}
    await websocket_manager.send_data(payload)
    return JSONResponse(content={})


@router.post(
    "/config/set",
    response_model=Message,
    status_code=status.HTTP_201_CREATED,
    summary="set configuration",
    description="Set configuration",
    response_description="configuration set",
    dependencies=[Depends(JWTBearer())],
)
async def set_configuration(
    config: Dict = Body(
        default=None,
        description="Configuration to set",
        example='{"confirm_listening": false }',
    ),
) -> JSONResponse:
    """Request to set the configuration

    :return: HTTP status code
    :rtype: int
    """
    LOG.info("SETTING CONFIGURATION: %s", config)
    return JSONResponse(content=system.set_config(config))
