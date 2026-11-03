import { describe, expect, it } from 'vitest';
import { agentHue, agentPalette } from './agent-palette';

describe('agentPalette', () => {
  it('gives each agent its own stable colours, in hex the planet can mix', () => {
    const designer = agentPalette('designer');
    expect(agentPalette('designer')).toEqual(designer);
    expect(agentHue('designer')).not.toBe(agentHue('code_reviewer'));
    for (const colour of Object.values(designer)) expect(colour).toMatch(/^#[0-9a-f]{6}$/);
  });
});
