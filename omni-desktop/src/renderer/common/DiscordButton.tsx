import { IconButton, Link } from '@invoke-ai/ui-library';
import { memo } from 'react';
import { PiDiscordLogoFill } from 'react-icons/pi';

export const DiscordButton = memo(() => {
  return (
    <Link href="https://discord.gg/ZmtBAhwWhy" target="_blank">
      <IconButton
        variant="link"
        minW={10}
        minH={10}
        colorScheme="base"
        aria-label="Discord"
        icon={<PiDiscordLogoFill />}
      />
    </Link>
  );
});
DiscordButton.displayName = 'DiscordButton';
