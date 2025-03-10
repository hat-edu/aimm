from functools import partial
from hat import aio
from pathlib import Path
import sqlite3

from aimm.server import common
from aimm import plugins


async def create(conf, group, _):
    common.json_schema_repo.validate('aimm://server/backend/sqlite.yaml#',
                                     conf)
    backend = SQLiteBackend()

    executor = aio.create_executor(1)
    connection = await executor(_ext_db_connect, Path(conf['path']))
    connection.row_factory = sqlite3.Row

    group.spawn(aio.call_on_cancel, executor, _ext_db_close, connection)

    backend._executor = executor
    backend._connection = connection
    backend._group = group

    return backend


class SQLiteBackend(common.Backend):

    @property
    def async_group(self) -> aio.Group:
        """Async group"""
        return self._group

    async def get_models(self):
        query = """SELECT * FROM models"""
        rows = await self._execute(query)
        models = [await self._row_to_model(row) for row in rows]
        return models

    async def create_model(self, model):
        instance_blob = await self._executor(plugins.exec_serialize,
                                             model.model_type, model.instance)
        query = """INSERT INTO models
                   (id, type, instance) VALUES
                   (:instance_id, :model_type, :instance)"""
        await self._execute(query,
                            instance_id=model.instance_id,
                            model_type=model.model_type,
                            instance=instance_blob)

    async def update_model(self, model):
        instance_blob = await self._executor(
            plugins.exec_serialize, model.model_type, model.instance)
        query = """UPDATE models
                   SET instance=:instance
                   WHERE id=:instance_id"""
        await self._execute(query,
                            instance=instance_blob,
                            instance_id=model.instance_id)

    async def _execute(self, query, **kwargs):
        return await self._executor(
            partial(_ext_db_execute, self._connection, query, **kwargs))

    async def _row_to_model(self, row):
        model_type = row['type']
        return common.Model(
            instance=await self._executor(
                plugins.exec_deserialize, model_type, row['instance']),
            model_type=model_type,
            instance_id=row['id'])


def _ext_db_connect(path):
    path.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect('file:{}?nolock=1'.format(str(path)), uri=True,
                           isolation_level=None,
                           detect_types=sqlite3.PARSE_DECLTYPES)
    _ext_db_execute(conn, """
        CREATE TABLE IF NOT EXISTS models (
            id INTEGER PRIMARY KEY,
            type TEXT NOT NULL,
            instance BLOB
        );""")
    return conn


def _ext_db_execute(conn, query, **kwargs):
    cursor = conn.execute(query, kwargs)
    conn.commit()
    return cursor.fetchall()


def _ext_db_close(conn):
    conn.close()
