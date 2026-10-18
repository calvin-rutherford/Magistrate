// Local Expo 0.0.6 consumer migration; see SOURCE.json and LICENSE.
import { KeyObject, X509Certificate } from 'node:crypto';
import * as x509 from '@peculiar/x509';
export interface KeyPair { privateKey: KeyObject; publicKey: KeyObject; }
export interface KeyPairPEM { privateKeyPEM: string; publicKeyPEM: string; }
export declare const expoProjectInformationOID: string;
export declare class Certificate {
  constructor(pem: string);
  readonly native: X509Certificate;
  readonly parsed: x509.X509Certificate;
  readonly publicKey: KeyObject;
  readonly validity: { notBefore: Date; notAfter: Date };
}
export declare function generateKeyPair(): KeyPair;
export declare function convertKeyPairToPEM(pair: KeyPair): KeyPairPEM;
export declare function convertKeyPairPEMToKeyPair(pair: KeyPairPEM): KeyPair;
export declare function convertPublicKeyPEMToPublicKey(pem: string): KeyObject;
export declare function convertPrivateKeyPEMToPrivateKey(pem: string): KeyObject;
export declare function convertCertificatePEMToCertificate(pem: string): Certificate;
export declare function convertCertificateToCertificatePEM(cert: Certificate): string;
export declare function convertCSRPEMToCSR(pem: string): x509.Pkcs10CertificateRequest;
export declare function convertCSRToCSRPEM(csr: x509.Pkcs10CertificateRequest): string;
export declare function generateSelfSignedCodeSigningCertificate(options: {
  keyPair: KeyPair; validityNotBefore: Date; validityNotAfter: Date; commonName: string;
}): Promise<Certificate>;
export declare function validateSelfSignedCertificate(cert: Certificate, pair: KeyPair): void;
export declare function signBufferRSASHA256AndVerify(key: KeyObject, cert: Certificate, data: Buffer): string;
export declare function generateCSR(pair: KeyPair, commonName: string): Promise<x509.Pkcs10CertificateRequest>;
export declare function generateDevelopmentCertificateFromCSR(key: KeyObject, issuer: Certificate,
  csr: x509.Pkcs10CertificateRequest, appId: string, scopeKey: string): Promise<Certificate>;
