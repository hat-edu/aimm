"""REPL client module. Provides a minimal interface that follows the protocol
specified by the REPL control."""

import base64
from getpass import getpass
import hashlib
from hat import juggler
from hat import aio
import numpy
import pandas
import typing

from aimm import plugins


class AIMM(aio.Resource):
    """Class that manages connections to AIMM REPL control, directly maps
    available functions to its methods
    """

    def __init__(self):
        self._address = None
        self._group = aio.Group()
        self._connection = None
        self._state = None

    @property
    def async_group(self) -> aio.Group:
        """Async group"""
        return self._group

    @property
    def address(self) -> typing.Optional[str]:
        """Current address object is connected to"""
        return self._address

    @property
    def state(self) -> 'JSON':
        """Current state reported from the AIMM server"""
        return self._state

    async def connect(self, address: str, autoflush_delay: float = 0.2):
        """Connects to the specified remote address. Login data is received
        from a user prompt. Passwords are hashed with SHA-256 before sending
        login request.

        Args:
            address: remote server address"""
        connection = await juggler.connect(
            address, autoflush_delay=autoflush_delay)
        self._address = address

        username = input('Username: ')
        password_hash = hashlib.sha256()
        password_hash.update(getpass('Password: ').encode('utf-8'))
        await connection.send({'type': 'login',
                               'data': {
                                   'username': username,
                                   'password': password_hash.hexdigest()}})
        msg = await connection.receive()

        if msg != {'type': 'login_success'}:
            print('Login failed')
            return

        self._connection = connection
        self._group.spawn(connection.wait_closed).add_done_callback(
            lambda _: self._clear_connection())
        self._connection.register_change_cb(self._on_remote_state_change)

    async def create_instance(self,
                              model_type: str,
                              *args: 'PluginArg',
                              **kwargs: 'PluginArg'
                              ) -> 'JSON':
        """Creates a model instance on the remote server

        Args:
            model_type: model type
            args: instance creation parameters
            kwargs: instance creation parameters

        Returns:
            response msg"""
        args = [_arg_to_json(a) for a in args]
        kwargs = {k: _arg_to_json(v) for k, v in kwargs.items()}
        await self._connection.send({'type': 'create_instance',
                                     'data': {'model_type': model_type,
                                              'args': args,
                                              'kwargs': kwargs}})
        msg = await self._connection.receive()
        model_data = msg['model']
        return Model(self, model_data['instance_id'], model_data['model_type'])

    async def add_instance(self,
                           model_type: str,
                           instance: typing.Any) -> 'JSON':
        """Adds an existing instance on the remote server

        Args:
            model_type: model type
            instance: concrete instance

        Returns:
            response msg"""
        await self._connection.send(
            {'type': 'add_instance',
             'data': {'model_type': model_type,
                      'instance': _instance_to_b64(instance, model_type)}})
        return await self._connection.receive()

    async def update_instance(self,
                              model_type: str,
                              instance_id: int,
                              instance: typing.Any) -> 'JSON':
        """Replaces an existing instance with a new one

        Args:
            model_type: model type
            instance_id: ID of the instance that is being updated
            instance: new instance

        Returns:
            response msg"""
        await self._connection.send(
            {'type': 'update_instance',
             'data': {'instance_id': instance_id,
                      'model_type': model_type,
                      'instance': _instance_to_b64(instance, model_type)}})
        return await self._connection.receive()

    async def fit(self,
                  instance_id: int,
                  *args: 'PluginArg',
                  **kwargs: 'PluginArg') -> 'JSON':
        """Fits an instance on the remote server

        Args:
            instance_id: ID of the instance being fitted
            args: plugin-specific parameters for the fitting process
            kwargs: plugin-specific parameters for the fitting process

        Returns:
            response msg"""
        args = [_arg_to_json(a) for a in args]
        kwargs = {k: _arg_to_json(v) for k, v in kwargs.items()}
        await self._connection.send({'type': 'fit',
                                     'data': {'instance_id': instance_id,
                                              'args': args,
                                              'kwargs': kwargs}})
        await self._connection.receive()

    async def predict(self,
                      instance_id: int,
                      *args: 'PluginArg',
                      **kwargs: 'PluginArg') -> 'JSON':
        """Uses an instance on the remote server for a prediction

        Args:
            instance_id: ID of the instance used
            args: plugin-specific parameters for the prediction process
            kwargs: plugin-specific parameters for the prediction process

        Returns:
            response msg"""
        args = [_arg_to_json(a) for a in args]
        kwargs = {k: _arg_to_json(v) for k, v in kwargs.items()}
        await self._connection.send({'type': 'predict',
                                     'data': {'instance_id': instance_id,
                                              'args': args,
                                              'kwargs': kwargs}})
        result = await self._connection.receive()
        if result['success']:
            return _result_from_json(result['result'])
        return result

    def _clear_connection(self):
        self._connection = None

    def _on_remote_state_change(self):
        self._state = {'models': {}, 'actions': {}}
        remote_state = self._connection.remote_data
        self._state['models'] = {int(k): Model(self, k, v['model_type'])
                                 for k, v in remote_state['models'].items()}
        self._state['actions'] = {int(k): v
                                  for k, v in remote_state['actions'].items()}


class Model:
    """Represents an AIMM model instance and provides a simplified interface
    for using or changing it remotely.

    Args:
        aimm: AIMM connection instance
        instance_id: model instance id
        model_type: model type
        """

    def __init__(self, aimm: AIMM, instance_id: int, model_type: str):
        self._aimm = aimm
        self._instance_id = instance_id
        self._model_type = model_type

    async def fit(self, *args: 'PluginArg', **kwargs: 'PluginArg'):
        """
        Fits the model

        Args:
            *args: positional arguments for fitting
            **kwargs: named arguments for fitting"""
        await self._aimm.fit(self._instance_id, *args, **kwargs)

    async def predict(self, *args: 'PluginArg',
                      **kwargs: 'PluginArg') -> typing.Any:
        """
        Uses the model to generate a prediction

        Args:
            *args: positional arguments for predicting
            **kwargs: named arguments for predicting"""
        return await self._aimm.predict(self._instance_id, *args, **kwargs)

    def __repr__(self):
        return (f'aimm.client.repl.Model<{self._model_type}>'
                f'(instance_id={self._instance_id})')


class DataAccessArg(typing.NamedTuple):
    """If passed as an argument, remote server calls a data access plugin and
    passes its result instead of this object"""

    name: str
    """name of the remote data access plugin"""
    args: typing.List['PluginArg'] = []
    """positional arguments for the data access plugin call"""
    kwargs: typing.Dict[str, 'PluginArg'] = {}
    """keyword arguments for the data access plugin call"""


JSON: typing.Type = typing.NewType(
    'JSON', typing.Union[None, bool, int, float, str, typing.List['JSON'],
                         typing.Dict[str, 'JSON']])
"""JSON serializable data"""

PluginArg: typing.Type = typing.NewType(
    'PluginArg', typing.Union['DataAccessArg', numpy.array, pandas.DataFrame,
                              pandas.Series, JSON])
"""Represents a generic, plugin-specific argument"""


def _arg_to_json(arg):
    if isinstance(arg, DataAccessArg):
        return {'type': 'data_access',
                'name': arg.name,
                'args': arg.args,
                'kwargs': arg.kwargs}
    if isinstance(arg, numpy.ndarray):
        return {'type': 'numpy_array',
                'dtype': str(arg.dtype),
                'data': arg.tolist()}
    if isinstance(arg, pandas.DataFrame):
        return {'type': 'pandas_dataframe',
                'data': arg.to_dict()}
    if isinstance(arg, pandas.Series):
        return {'type': 'pandas_series',
                'data': arg.tolist()}
    return arg


def _result_from_json(v):
    if not isinstance(v, dict):
        return v
    if v.get('type') == 'numpy_array':
        return numpy.array(v['data'])
    if v.get('type') == 'pandas_dataframe':
        return pandas.DataFrame.from_dict(v['data'])
    if v.get('type') == 'pandas_series':
        return pandas.Series(v['data'])
    return v


def _instance_to_b64(instance, model_type):
    return base64.b64encode(
        plugins.exec_serialize(model_type, instance)).decode('utf-8')


def _instance_from_b64(instance_b64, model_type):
    return base64.b64decode(
        plugins.exec_deserialize(model_type, instance_b64))
