from ok import PostMessageInteraction
from ok.device.intercation import PynputInteraction


class NTEOneTimeTask:

    def run(self):
        if isinstance(self.executor.interaction, PostMessageInteraction):
            self.executor.interaction.activate()
        elif isinstance(self.executor.interaction, PynputInteraction):
            self.bring_to_front()
        self.sleep(0.5)