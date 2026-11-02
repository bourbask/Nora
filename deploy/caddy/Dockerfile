# Caddy avec le module DNS du registraire, pour des certificats obtenus par
# défi DNS : aucun port public n'est nécessaire.
FROM caddy:2.10-builder AS build
RUN xcaddy build --with github.com/caddy-dns/ovh

FROM caddy:2.10
COPY --from=build /usr/bin/caddy /usr/bin/caddy
