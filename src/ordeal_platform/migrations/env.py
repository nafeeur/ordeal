from alembic import context
from ordeal_platform.db import metadata


def run():
    connection = context.config.attributes["connection"]
    context.configure(connection=connection, target_metadata=metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()

run()
