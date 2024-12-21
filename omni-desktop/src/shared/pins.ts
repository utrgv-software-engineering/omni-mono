import { compare } from '@renovatebot/pep440';
import { assert, objectKeys } from 'tsafe';

type PlatformIndices = {
  cuda?: string;
  cpu?: string;
  rocm?: string;
};

type Pins = {
  python: string;
  torchIndexUrl: {
    win32: PlatformIndices;
    linux: PlatformIndices;
    darwin: PlatformIndices;
  };
};

const PACKAGE_PINS: Record<string, Pins> = {
  '5.0.0': {
    python: '3.11',
    torchIndexUrl: {
      win32: {
        cuda: 'https://download.pytorch.org/whl/cu124',
      },
      linux: {
        cpu: 'https://download.pytorch.org/whl/cpu',
        rocm: 'https://download.pytorch.org/whl/rocm62',
      },
      darwin: {},
    },
  },
  '0.0.0': {
    python: '3.11',
    torchIndexUrl: {
      win32: {
        cuda: 'https://download.pytorch.org/whl/cu124',
      },
      linux: {
        cpu: 'https://download.pytorch.org/whl/cpu',
        rocm: 'https://download.pytorch.org/whl/rocm52',
      },
      darwin: {},
    },
  },
};

export const getPins = (targetVersion: string): Pins => {
  const versions = objectKeys(PACKAGE_PINS);
  const sortedVersions = versions.sort(compare).toReversed();
  const pinKey = sortedVersions.find((version) => compare(version, targetVersion) <= 0);
  assert(pinKey !== undefined, `No pins found for version ${targetVersion}`);
  const pins = PACKAGE_PINS[pinKey];
  assert(pins !== undefined, `No pins found for version ${targetVersion}`);
  return pins;
};
