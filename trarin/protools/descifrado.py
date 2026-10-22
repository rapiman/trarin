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
    n = len(datos)
    if tipo == 0x01:
        # el byte i se combina con clave[i & 0xFF]: la clave repetida a lo largo del archivo
        flujo = (clave * (n // 256 + 1))[:n]
    else:
        # Pro Tools 10+: la clave cambia cada 4096 bytes (bloque j: clave[j & 0xFF])
        flujo = b"".join(bytes([clave[j & 0xFF]]) * 4096 for j in range(n // 4096 + 1))[:n]
    # el XOR de todo el archivo de una vez, como números enteros grandes
    cuerpo = (int.from_bytes(datos[0x14:], "little") ^ int.from_bytes(flujo[0x14:], "little"))
    return bytes(datos[:0x14]) + cuerpo.to_bytes(n - 0x14, "little")


def leer_descifrado(ruta) -> bytes:
    return descifrar(Path(ruta).read_bytes())
