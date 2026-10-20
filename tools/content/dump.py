from pe import PE
import struct,sys
p=PE()
def ann(va,n):
    o=p.va2off(va)
    for k in range(0,n,8):
        b=p.data[o+k:o+k+8]
        q=struct.unpack('<Q',b)[0]; i=struct.unpack('<2I',b); f=struct.unpack('<2f',b)
        s=''
        if 0x140000000<q<0x150000000:
            ln=struct.unpack_from('<Q',p.data,o+k+8)[0]
            if 0<ln<300: s='-> '+repr(p.str_at_va(q,ln)[:60])
            else: s='-> ptr'
        print(f'{va+k:x} +{k:4d} {q:#018x} i={i} f=({f[0]:.4g},{f[1]:.4g}) {s}')
if __name__=='__main__':
    ann(int(sys.argv[1],16),int(sys.argv[2]))
