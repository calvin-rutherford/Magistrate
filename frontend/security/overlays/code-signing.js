'use strict';
// Expo code-signing-certificates 0.0.6 consumer migration, not a Forge fork.
// Original API/workflow: Expo (MIT, see LICENSE). Crypto primitives are Node's
// OpenSSL/WebCrypto; X.509/PKCS#10 encoding uses @peculiar/x509 2.2.0 (MIT).
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
require('reflect-metadata');
const x509 = require('@peculiar/x509');
const { fromBER } = require('asn1js');
const algorithm = { name: 'RSASSA-PKCS1-v1_5', hash: 'SHA-256' };
const parseOptions = { berOptions: { maxDepth: 32, maxNodes: 4096, maxContentLength: 1024 * 1024 } };
const expoProjectInformationOID = '1.2.840.113556.1.8000.2554.43437.254.128.102.157.7894389.20439.2.1';

function rsa(key, type) {
  assert(key instanceof crypto.KeyObject && key.type === type && key.asymmetricKeyType === 'rsa', 'An RSA key is required');
  assert(key.asymmetricKeyDetails.modulusLength >= 2048, 'RSA keys must have at least 2048 bits');
  return key;
}
function pemDER(value, label) {
  assert(typeof value === 'string' && value.length <= 1024 * 1024, 'Invalid or oversized PEM');
  const match = value.trim().match(new RegExp(`^-----BEGIN ${label}-----\\r?\\n([A-Za-z0-9+/=\\r\\n]+)\\r?\\n-----END ${label}-----$`));
  assert(match, `Expected exactly one ${label} PEM block`);
  const encoded = match[1].replace(/[\r\n]/g, '');
  const bytes = Buffer.from(encoded, 'base64');
  assert(bytes.toString('base64') === encoded, 'Noncanonical PEM base64');
  return bytes;
}
function sameKey(a, b) {
  return a.export({ format: 'der', type: 'spki' }).equals(b.export({ format: 'der', type: 'spki' }));
}
function assertPair({ privateKey, publicKey }) {
  rsa(privateKey, 'private'); rsa(publicKey, 'public');
  assert(sameKey(crypto.createPublicKey(privateKey), publicKey), 'keyPair key mismatch');
}
async function webKeys(pair) {
  assertPair(pair);
  return {
    privateKey: await crypto.webcrypto.subtle.importKey('pkcs8', pair.privateKey.export({ format: 'der', type: 'pkcs8' }), algorithm, false, ['sign']),
    publicKey: await crypto.webcrypto.subtle.importKey('spki', pair.publicKey.export({ format: 'der', type: 'spki' }), algorithm, true, ['verify']),
  };
}
class Certificate {
  constructor(pem) {
    const der = pemDER(pem, 'CERTIFICATE');
    this.native = new crypto.X509Certificate(der);
    assert(this.native.raw.equals(der), 'Trailing certificate bytes');
    this.parsed = new x509.X509Certificate(der, parseOptions);
    assert(new Set(this.parsed.extensions.map(extension => extension.type)).size === this.parsed.extensions.length, 'Duplicate certificate extension');
  }
  get publicKey() { return rsa(this.native.publicKey, 'public'); }
  get validity() { return { notBefore: this.native.validFromDate, notAfter: this.native.validToDate }; }
}
function generateKeyPair() {
  return crypto.generateKeyPairSync('rsa', { modulusLength: 2048, publicExponent: 65537 });
}
function convertKeyPairToPEM(pair) {
  assertPair(pair);
  return {
    privateKeyPEM: pair.privateKey.export({ format: 'pem', type: 'pkcs1' }),
    publicKeyPEM: pair.publicKey.export({ format: 'pem', type: 'pkcs1' }),
  };
}
function convertPrivateKeyPEMToPrivateKey(pem) { return rsa(crypto.createPrivateKey(pem), 'private'); }
function convertPublicKeyPEMToPublicKey(pem) { return rsa(crypto.createPublicKey(pem), 'public'); }
function convertKeyPairPEMToKeyPair({ privateKeyPEM, publicKeyPEM }) {
  const pair = { privateKey: convertPrivateKeyPEMToPrivateKey(privateKeyPEM), publicKey: convertPublicKeyPEMToPublicKey(publicKeyPEM) };
  assertPair(pair);
  return pair;
}
function convertCertificatePEMToCertificate(pem) { return new Certificate(pem); }
function convertCertificateToCertificatePEM(cert) { return cert.native.toString(); }
function convertCSRPEMToCSR(pem) {
  const der = pemDER(pem, 'CERTIFICATE REQUEST');
  // X.509's schema decoder accepts the first ASN.1 value and rawData retains
  // the original input, so rawData equality cannot prove full consumption.
  // Use the same maintained, resource-bounded decoder's consumed-byte offset.
  const decoded = fromBER(der, parseOptions.berOptions);
  assert(decoded.offset >= 0 && !decoded.result.error, 'Invalid CSR ASN.1');
  assert.equal(decoded.offset, der.length, 'Trailing CSR bytes');
  return new x509.Pkcs10CertificateRequest(der, parseOptions);
}
function convertCSRToCSRPEM(csr) { return csr.toString('pem'); }
function serial() {
  const bytes = crypto.randomBytes(9); bytes[0] &= 0x7f;
  // Positive and nonzero, preserving the upstream 9-byte serial size.
  bytes[8] |= 1;
  return bytes.toString('hex');
}
function codeSigningExtensions() {
  return [new x509.KeyUsagesExtension(x509.KeyUsageFlags.digitalSignature, true),
    new x509.ExtendedKeyUsageExtension([x509.ExtendedKeyUsage.codeSigning], true)];
}
async function generateSelfSignedCodeSigningCertificate({ keyPair, validityNotBefore, validityNotAfter, commonName }) {
  assert(validityNotBefore instanceof Date && validityNotAfter instanceof Date && validityNotAfter > validityNotBefore, 'validityNotAfter must be later than validityNotBefore');
  const cert = await x509.X509CertificateGenerator.createSelfSigned({
    serialNumber: serial(), name: [{ CN: [commonName] }], keys: await webKeys(keyPair),
    notBefore: validityNotBefore, notAfter: validityNotAfter, extensions: codeSigningExtensions(),
    signingAlgorithm: algorithm,
  }, crypto.webcrypto);
  return new Certificate(cert.toString('pem'));
}
function validateSelfSignedCertificate(certificate, keyPair) {
  assertPair(keyPair);
  const native = certificate.native;
  assert(native.subject === native.issuer, 'Certificate is not self-signed');
  const now = new Date();
  assert(certificate.validity.notBefore <= now && certificate.validity.notAfter >= now, 'Certificate validity expired');
  const usage = certificate.parsed.getExtension(x509.KeyUsagesExtension);
  assert(usage && (usage.usages & x509.KeyUsageFlags.digitalSignature), 'X509v3 Key Usage: Digital Signature not present');
  const extended = certificate.parsed.getExtension(x509.ExtendedKeyUsageExtension);
  assert(extended && extended.usages.includes(x509.ExtendedKeyUsage.codeSigning), 'X509v3 Extended Key Usage: Code Signing not present');
  assert(native.verify(certificate.publicKey), 'Certificate signature not valid');
  assert(sameKey(certificate.publicKey, keyPair.publicKey), 'Certificate public key does not match key pair public key');
}
function signBufferRSASHA256AndVerify(privateKey, certificate, bufferToSign) {
  rsa(privateKey, 'private');
  assert(Buffer.isBuffer(bufferToSign), 'Expected manifest bytes');
  const signature = crypto.sign('sha256', bufferToSign, { key: privateKey, padding: crypto.constants.RSA_PKCS1_PADDING });
  assert(crypto.verify('sha256', bufferToSign, { key: certificate.publicKey, padding: crypto.constants.RSA_PKCS1_PADDING }, signature), 'Signature generated with private key not valid for certificate');
  return signature.toString('base64');
}
async function generateCSR(keyPair, commonName) {
  return x509.Pkcs10CertificateRequestGenerator.create({
    name: [{ CN: [commonName] }], keys: await webKeys(keyPair), signingAlgorithm: algorithm,
  }, crypto.webcrypto);
}
async function generateDevelopmentCertificateFromCSR(issuerPrivateKey, issuerCertificate, csr, appId, scopeKey) {
  assert(csr instanceof x509.Pkcs10CertificateRequest && await csr.verify(crypto.webcrypto), 'CSR not self-signed');
  assertPair({ privateKey: issuerPrivateKey, publicKey: issuerCertificate.publicKey });
  assert(issuerCertificate.native.ca, 'Issuer certificate must be a CA');
  const now = Date.now();
  assert(issuerCertificate.validity.notBefore.getTime() <= now && issuerCertificate.validity.notAfter.getTime() >= now, 'Issuer certificate validity expired');
  const notBefore = new Date(now - 24 * 60 * 60 * 1000);
  const notAfter = new Date(Math.min(notBefore.getTime() + 30 * 24 * 60 * 60 * 1000, issuerCertificate.validity.notAfter.getTime()));
  const publicKey = crypto.createPublicKey({ key: Buffer.from(csr.publicKey.rawData), type: 'spki', format: 'der' });
  rsa(publicKey, 'public');
  const keys = await webKeys({ privateKey: issuerPrivateKey, publicKey: issuerCertificate.publicKey });
  const certificate = await x509.X509CertificateGenerator.create({
    serialNumber: serial(), subject: csr.subjectName, issuer: issuerCertificate.parsed.subjectName,
    publicKey: csr.publicKey, signingKey: keys.privateKey, signingAlgorithm: algorithm, notBefore, notAfter,
    extensions: [...codeSigningExtensions(), new x509.Extension(expoProjectInformationOID, false, Buffer.from(`${appId},${scopeKey}`, 'utf8'))],
  }, crypto.webcrypto);
  return new Certificate(certificate.toString('pem'));
}
module.exports = { expoProjectInformationOID, Certificate, generateKeyPair, convertKeyPairToPEM,
  convertKeyPairPEMToKeyPair, convertPublicKeyPEMToPublicKey, convertPrivateKeyPEMToPrivateKey,
  convertCertificatePEMToCertificate, convertCertificateToCertificatePEM, convertCSRPEMToCSR,
  convertCSRToCSRPEM, generateSelfSignedCodeSigningCertificate, validateSelfSignedCertificate,
  signBufferRSASHA256AndVerify, generateCSR, generateDevelopmentCertificateFromCSR };
