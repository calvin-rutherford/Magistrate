import { Href, Link } from 'expo-router';
import { Alert } from 'react-native';
import { type ComponentProps } from 'react';
import { openExternalUrl } from '../utils/externalLinks';

type Props = Omit<ComponentProps<typeof Link>, 'href'> & { href: Href & string };

export function ExternalLink({ href, ...rest }: Props) {
  return (
    <Link
      target="_blank"
      {...rest}
      href={href}
      onPress={async (event) => {
        event.preventDefault();
        if (process.env.EXPO_OS !== 'web') {
          const result = await openExternalUrl(href);
          if (!result.ok) Alert.alert('Unable to open link', result.message);
          return;
        }
        const result = await openExternalUrl(href);
        if (!result.ok) Alert.alert('Unable to open link', result.message);
      }}
    />
  );
}
