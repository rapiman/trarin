# Descifrado de sesiones Pro Tools (.ptf/.ptx).
#
# Portado a Python desde libptformat (Damien Zammit, https://github.com/zamaudio/ptformat),
# distribuido bajo GNU LGPL 2.1 o posterior. Este módulo mantiene esa licencia.
"""Quita la capa XOR de un archivo de sesión de Pro Tools."""

from pathlib import Path


class ErrorDescifrado(Exception):
    pass


def _delta(valor: int, mult: int, negativo: bool) -> int:
    for i in range(256):
        if (i * mult) & 0xFF == valor:
            return (-i) & 0xFF if negativo else i
    return 0


def descifrar(datos: bytes) -> bytes:
    """Devuelve los bytes de la sesión sin cifrar.

    Los primeros 20 bytes van en claro. El byte 0x12 indica el tipo de XOR:
    0x01 para Pro Tools 5 a 9, 0x05 para Pro Tools 10 en adelante.
    """
    if len(datos) < 0x14:
        raise ErrorDescifrado("archivo demasiado corto para ser una sesión")
    tipo, valor = datos[0x12], datos[0x13]
    if tipo == 0x01:
        delta = _delta(valor, 53, False)
    elif tipo == 0x05:
        delta = _delta(valor, 11, True)
    else:
        raise ErrorDescifrado(f"tipo de cifrado desconocido: 0x{tipo:02x}")
    clave = bytes((i * delta) & 0xFF for i in range(256))
    salida = bytearray(datos)
    if tipo == 0x01:
        for i in range(0x14, len(datos)):
            salida[i] ^= clave[i & 0xFF]
    else:
        # Pro Tools 10+: la clave cambia cada 4096 bytes.
        inicio = 0x14
        while inicio < len(datos):
            fin = min(len(datos), (inicio // 4096 + 1) * 4096)
            k = clave[(inicio >> 12) & 0xFF]
            salida[inicio:fin] = bytes(x ^ k for x in salida[inicio:fin])
            inicio = fin
    return bytes(salida)


def leer_descifrado(ruta) -> bytes:
    return descifrar(Path(ruta).read_bytes())
