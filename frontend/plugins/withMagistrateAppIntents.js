const fs = require('fs');
const path = require('path');
const { IOSConfig, withDangerousMod, withXcodeProject } = require('@expo/config-plugins');

const SOURCE_NAME = 'MagistrateAppIntents.swift';

/**
 * Compile App Intents into the main iOS application target. Keeping them in
 * the app target avoids a background-capable extension: every intent opens an
 * authenticated, foreground app-owned deep link before microphone/network use.
 */
module.exports = function withMagistrateAppIntents(config) {
  config = withDangerousMod(config, ['ios', async mod => {
    const iosRoot = mod.modRequest.platformProjectRoot;
    const projectName = IOSConfig.XcodeUtils.getHackyProjectName(iosRoot, mod);
    const destination = path.join(iosRoot, projectName, SOURCE_NAME);
    const source = path.join(mod.modRequest.projectRoot, 'native', 'ios', SOURCE_NAME);
    fs.mkdirSync(path.dirname(destination), { recursive: true });
    fs.copyFileSync(source, destination);
    return mod;
  }]);
  return withXcodeProject(config, mod => {
    const iosRoot = mod.modRequest.platformProjectRoot;
    const projectName = IOSConfig.XcodeUtils.getHackyProjectName(iosRoot, mod);
    IOSConfig.XcodeUtils.addBuildSourceFileToGroup({
      filepath: `${projectName}/${SOURCE_NAME}`,
      groupName: projectName,
      project: mod.modResults,
    });
    return mod;
  });
};
