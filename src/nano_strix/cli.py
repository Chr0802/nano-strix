import click


@click.group()
@click.version_option()
def main():
    """nano-strix CLI tool."""


@main.command()
def hello():
    """Say hello."""
    click.echo("Hello from nano-strix!")
