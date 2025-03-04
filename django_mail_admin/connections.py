from threading import local

from django.core.mail import get_connection

from .settings import get_backend


# Copied from Django 1.8's django.core.cache.CacheHandler
class ConnectionHandler(object):
    """
    A Cache Handler to manage access to Cache instances.

    Ensures only one instance of each alias exists per thread.
    """

    def __init__(self):
        self._connections = local()

    def __getitem__(self, maybe_hacked_alias):
        try:
            return self._connections.connections[maybe_hacked_alias]
        except AttributeError:
            self._connections.connections = {}
        except KeyError:
            pass

        # as a hack other places are using backend_alias;;;from_oauth_user
        # or backend_alias???from_email
        real_alias = maybe_hacked_alias
        from_oauth_user = None
        from_email = None
        if "???" in maybe_hacked_alias:
            real_alias,from_email = maybe_hacked_alias.split("???")
        elif ";;;" in maybe_hacked_alias:
            real_alias,from_oauth_user = maybe_hacked_alias.split(";;;")

        try:
            backend = get_backend(real_alias)
        except KeyError:
            raise KeyError('%s is not a valid backend alias' % real_alias)

        # backend_class is a EmailBackend subclass like O365Backend or GmailOAuth2Backend
        backend_class = get_connection(backend)

        # now mutate the backend class after init since get_connection is within django
        backend_class.from_email = from_email
        backend_class.from_oauth_user = from_oauth_user

        backend_class.open()
        self._connections.connections[maybe_hacked_alias] = backend_class
        return backend_class

    def all(self):
        return getattr(self._connections, 'connections', {}).values()

    def close(self):
        for connection in self.all():
            connection.close()


connections = ConnectionHandler()
