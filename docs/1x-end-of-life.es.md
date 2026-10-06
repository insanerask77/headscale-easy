# Headscale Easy 1.x está discontinuada

!!! danger "Fin de vida: 6 de octubre de 2026"
    Headscale Easy **1.x queda discontinuada con la 2.0.0**. No recibe más correcciones, tampoco de
    seguridad. No la instales en un servidor nuevo y planifica salir de ella en los que ya la usan.

## Qué sigue disponible

- La última versión de 1.x es la **1.5.0**. Su etiqueta de git (`v1.5.0`) y sus imágenes
  (`ghcr.io/insanerask77/headscale-easy:1.5.0`) siguen donde estaban, tal como se publicaron.
- No se borra nada y no se va a parchear nada. Una vulnerabilidad que se encuentre en 1.x a partir de hoy no se corrige.
- El instalador de 1.x, su compose y su documentación ya no están en la rama principal del repositorio.
  Si hace falta, léelos en la etiqueta `v1.5.0`.

## Qué cambia en la 2.0

La 2.0 se ejecuta de otra forma: un solo contenedor (Headscale, Caddy y la consola) que se instala con un
único archivo de Docker Compose y un asistente de primer arranque. Consulta
[Primeros pasos](getting-started.md) y los [cambios](changelog.md).

## Pasar a la 2.0

**No hay actualización en el sitio ni herramienta de conversión.** Instala la 2.0 como un despliegue nuevo y
añade después tus dispositivos. Conserva tu servidor 1.x y sus copias de seguridad hasta que el nuevo funcione.

## Si te quedas en 1.x

Usas software que nadie mantiene, en un papel (el plano de control de una VPN) donde eso importa. Mantenlo
detrás de un cortafuegos, no lo expongas a Internet y pasa a la 2.0 cuando puedas.
