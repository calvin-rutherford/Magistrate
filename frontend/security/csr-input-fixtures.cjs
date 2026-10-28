// Synthetic input framing shared by the original-Forge control and replacement
// consumer tests. No keys, original parser implementation or application imports.
function csrPEM(der, newline = '\n') {
  const lines = Buffer.from(der).toString('base64').match(/.{1,64}/g) || [];
  return ['-----BEGIN CERTIFICATE REQUEST-----', ...lines, '-----END CERTIFICATE REQUEST-----', ''].join(newline);
}
function trailingCSRInputs(der) {
  const suffixes = ['00', '0500', '0000', '3000', '020101', 'ff', '200d0a']
    .map(hex => ({ name: hex, suffix: Buffer.from(hex, 'hex') }));
  suffixes.push({ name: 'second-csr', suffix: Buffer.from(der) },
    { name: 'zero-padding', suffix: Buffer.alloc(4096) });
  return suffixes.map(({ name, suffix }) => ({ name, der: Buffer.concat([Buffer.from(der), suffix]) }));
}
module.exports = { csrPEM, trailingCSRInputs };
