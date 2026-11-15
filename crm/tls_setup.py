"""Generate local TLS material; never installs trust or changes firewall automatically."""
import ipaddress
import sys
from datetime import datetime,timedelta,timezone
from pathlib import Path
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from .network import local_addresses

ROOT=Path(__file__).resolve().parent.parent

def ensure_certificate(folder=None):
    addresses=[ipaddress.ip_address(value) for value in local_addresses()]
    if not addresses:
        raise RuntimeError('No private network address found. Connect this computer to Wi-Fi or Ethernet.')
    folder=folder or ROOT/'data/tls';folder.mkdir(parents=True,exist_ok=True)
    today=datetime.now(timezone.utc)
    address=addresses[0]
    if (folder/'server.crt').exists() and (folder/'server.key').exists() and (folder/'phone-ca.cer').exists():
        previous=x509.load_pem_x509_certificate((folder/'server.crt').read_bytes())
        covered=set(previous.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.IPAddress))
        if set(addresses).issubset(covered) and previous.not_valid_after_utc > today+timedelta(days=14):
            (folder/'address.txt').write_text(f'https://{address}:8443',encoding='utf-8')
            return False
    if (folder/'ca.key').exists():
        key=serialization.load_pem_private_key((folder/'ca.key').read_bytes(),password=None)
        cert=x509.load_pem_x509_certificate((folder/'ca.crt').read_bytes())
    else:
        key=rsa.generate_private_key(public_exponent=65537,key_size=3072)
        name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'Matilda CRM Local CA')])
        cert=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
              .serial_number(x509.random_serial_number()).not_valid_before(today-timedelta(minutes=5)).not_valid_after(today+timedelta(days=3650))
              .add_extension(x509.BasicConstraints(ca=True,path_length=0),critical=True)
              .add_extension(x509.KeyUsage(digital_signature=True,content_commitment=False,key_encipherment=False,data_encipherment=False,key_agreement=False,key_cert_sign=True,crl_sign=True,encipher_only=None,decipher_only=None),critical=True)
              .sign(key,hashes.SHA256()))
        (folder/'ca.key').write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
        (folder/'ca.crt').write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    leafkey=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    leaf=(x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,str(address))]))
          .issuer_name(cert.subject).public_key(leafkey.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(today-timedelta(minutes=5)).not_valid_after(today+timedelta(days=365))
          .add_extension(x509.SubjectAlternativeName([x509.IPAddress(value) for value in addresses]),critical=False)
          .add_extension(x509.BasicConstraints(ca=False,path_length=None),critical=True)
          .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]),critical=False)
          .sign(key,hashes.SHA256()))
    (folder/'server.key').write_bytes(leafkey.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    (folder/'server.crt').write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    (folder/'phone-ca.cer').write_bytes(cert.public_bytes(serialization.Encoding.DER))
    (folder/'address.txt').write_text(f'https://{address}:8443',encoding='utf-8')
    return True

def main():
    ensure_certificate()
    address=local_addresses()[0]
    print(f'Phone address: https://{address}:8443')
    print('Transfer ONLY data/tls/phone-ca.cer to your phone and trust the CA. Never share .key files.')
    print('Then run START_PHONE_ACCESS.cmd. Windows firewall may ask to allow private-network access.')

if __name__=='__main__':main()
