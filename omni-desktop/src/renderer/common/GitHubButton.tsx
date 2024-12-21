import { IconButton, Link } from '@invoke-ai/ui-library';
import { memo } from 'react';
import { PiGithubLogoFill } from 'react-icons/pi';

export const GitHubButton = memo(() => {
  return (
    <Link href="https://github.com/invoke-ai/InvokeAI" target="_blank">
      <IconButton
        variant="link"
        minW={10}
        minH={10}
        colorScheme="base"
        aria-label="GitHub"
        icon={<PiGithubLogoFill />}
      />
    </Link>
  );
});
GitHubButton.displayName = 'GitHubButton';
