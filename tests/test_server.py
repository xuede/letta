import json
import os
import shutil
import uuid
import warnings
from typing import List, Tuple

import pytest
from sqlalchemy import delete

import letta.utils as utils
from letta.constants import BASE_MEMORY_TOOLS, BASE_TOOLS
from letta.orm import Provider, Step
from letta.schemas.block import CreateBlock
from letta.schemas.enums import MessageRole
from letta.schemas.letta_message import LettaMessage, ReasoningMessage, SystemMessage, ToolCallMessage, ToolReturnMessage, UserMessage
from letta.schemas.llm_config import LLMConfig
from letta.schemas.providers import Provider as PydanticProvider
from letta.schemas.user import User

utils.DEBUG = True
from letta.config import LettaConfig
from letta.schemas.agent import CreateAgent, UpdateAgent
from letta.schemas.embedding_config import EmbeddingConfig
from letta.schemas.job import Job as PydanticJob
from letta.schemas.message import Message
from letta.schemas.source import Source as PydanticSource
from letta.server.server import SyncServer
from letta.system import unpack_message

from .utils import DummyDataConnector

WAR_AND_PEACE = """BOOK ONE: 1805

CHAPTER I

“Well, Prince, so Genoa and Lucca are now just family estates of the
Buonapartes. But I warn you, if you don't tell me that this means war,
if you still try to defend the infamies and horrors perpetrated by that
Antichrist—I really believe he is Antichrist—I will have nothing
more to do with you and you are no longer my friend, no longer my
'faithful slave,' as you call yourself! But how do you do? I see I
have frightened you—sit down and tell me all the news.”

It was in July, 1805, and the speaker was the well-known Anna Pávlovna
Schérer, maid of honor and favorite of the Empress Márya Fëdorovna.
With these words she greeted Prince Vasíli Kurágin, a man of high
rank and importance, who was the first to arrive at her reception. Anna
Pávlovna had had a cough for some days. She was, as she said, suffering
from la grippe; grippe being then a new word in St. Petersburg, used
only by the elite.

All her invitations without exception, written in French, and delivered
by a scarlet-liveried footman that morning, ran as follows:

“If you have nothing better to do, Count (or Prince), and if the
prospect of spending an evening with a poor invalid is not too terrible,
I shall be very charmed to see you tonight between 7 and 10—Annette
Schérer.”

“Heavens! what a virulent attack!” replied the prince, not in the
least disconcerted by this reception. He had just entered, wearing an
embroidered court uniform, knee breeches, and shoes, and had stars on
his breast and a serene expression on his flat face. He spoke in that
refined French in which our grandfathers not only spoke but thought, and
with the gentle, patronizing intonation natural to a man of importance
who had grown old in society and at court. He went up to Anna Pávlovna,
kissed her hand, presenting to her his bald, scented, and shining head,
and complacently seated himself on the sofa.

“First of all, dear friend, tell me how you are. Set your friend's
mind at rest,” said he without altering his tone, beneath the
politeness and affected sympathy of which indifference and even irony
could be discerned.

“Can one be well while suffering morally? Can one be calm in times
like these if one has any feeling?” said Anna Pávlovna. “You are
staying the whole evening, I hope?”

“And the fete at the English ambassador's? Today is Wednesday. I
must put in an appearance there,” said the prince. “My daughter is
coming for me to take me there.”

“I thought today's fete had been canceled. I confess all these
festivities and fireworks are becoming wearisome.”

“If they had known that you wished it, the entertainment would have
been put off,” said the prince, who, like a wound-up clock, by force
of habit said things he did not even wish to be believed.

“Don't tease! Well, and what has been decided about Novosíltsev's
dispatch? You know everything.”

“What can one say about it?” replied the prince in a cold, listless
tone. “What has been decided? They have decided that Buonaparte has
burnt his boats, and I believe that we are ready to burn ours.”

Prince Vasíli always spoke languidly, like an actor repeating a stale
part. Anna Pávlovna Schérer on the contrary, despite her forty years,
overflowed with animation and impulsiveness. To be an enthusiast had
become her social vocation and, sometimes even when she did not
feel like it, she became enthusiastic in order not to disappoint the
expectations of those who knew her. The subdued smile which, though it
did not suit her faded features, always played round her lips expressed,
as in a spoiled child, a continual consciousness of her charming defect,
which she neither wished, nor could, nor considered it necessary, to
correct.

In the midst of a conversation on political matters Anna Pávlovna burst
out:

“Oh, don't speak to me of Austria. Perhaps I don't understand
things, but Austria never has wished, and does not wish, for war. She
is betraying us! Russia alone must save Europe. Our gracious sovereign
recognizes his high vocation and will be true to it. That is the one
thing I have faith in! Our good and wonderful sovereign has to perform
the noblest role on earth, and he is so virtuous and noble that God will
not forsake him. He will fulfill his vocation and crush the hydra of
revolution, which has become more terrible than ever in the person of
this murderer and villain! We alone must avenge the blood of the just
one.... Whom, I ask you, can we rely on?... England with her commercial
spirit will not and cannot understand the Emperor Alexander's
loftiness of soul. She has refused to evacuate Malta. She wanted to
find, and still seeks, some secret motive in our actions. What answer
did Novosíltsev get? None. The English have not understood and cannot
understand the self-abnegation of our Emperor who wants nothing for
himself, but only desires the good of mankind. And what have they
promised? Nothing! And what little they have promised they will not
perform! Prussia has always declared that Buonaparte is invincible, and
that all Europe is powerless before him.... And I don't believe a
word that Hardenburg says, or Haugwitz either. This famous Prussian
neutrality is just a trap. I have faith only in God and the lofty
destiny of our adored monarch. He will save Europe!”

She suddenly paused, smiling at her own impetuosity.

“I think,” said the prince with a smile, “that if you had been
sent instead of our dear Wintzingerode you would have captured the King
of Prussia's consent by assault. You are so eloquent. Will you give me
a cup of tea?”

“In a moment. À propos,” she added, becoming calm again, “I am
expecting two very interesting men tonight, le Vicomte de Mortemart, who
is connected with the Montmorencys through the Rohans, one of the best
French families. He is one of the genuine émigrés, the good ones. And
also the Abbé Morio. Do you know that profound thinker? He has been
received by the Emperor. Had you heard?”

“I shall be delighted to meet them,” said the prince. “But
tell me,” he added with studied carelessness as if it had only just
occurred to him, though the question he was about to ask was the chief
motive of his visit, “is it true that the Dowager Empress wants
Baron Funke to be appointed first secretary at Vienna? The baron by all
accounts is a poor creature.”

Prince Vasíli wished to obtain this post for his son, but others were
trying through the Dowager Empress Márya Fëdorovna to secure it for
the baron.

Anna Pávlovna almost closed her eyes to indicate that neither she nor
anyone else had a right to criticize what the Empress desired or was
pleased with.

“Baron Funke has been recommended to the Dowager Empress by her
sister,” was all she said, in a dry and mournful tone.

As she named the Empress, Anna Pávlovna's face suddenly assumed an
expression of profound and sincere devotion and respect mingled with
sadness, and this occurred every time she mentioned her illustrious
patroness. She added that Her Majesty had deigned to show Baron Funke
beaucoup d'estime, and again her face clouded over with sadness.

The prince was silent and looked indifferent. But, with the womanly and
courtierlike quickness and tact habitual to her, Anna Pávlovna
wished both to rebuke him (for daring to speak as he had done of a man
recommended to the Empress) and at the same time to console him, so she
said:

“Now about your family. Do you know that since your daughter came
out everyone has been enraptured by her? They say she is amazingly
beautiful.”

The prince bowed to signify his respect and gratitude.

“I often think,” she continued after a short pause, drawing nearer
to the prince and smiling amiably at him as if to show that political
and social topics were ended and the time had come for intimate
conversation—“I often think how unfairly sometimes the joys of life
are distributed. Why has fate given you two such splendid children?
I don't speak of Anatole, your youngest. I don't like him,” she
added in a tone admitting of no rejoinder and raising her eyebrows.
“Two such charming children. And really you appreciate them less than
anyone, and so you don't deserve to have them.”

And she smiled her ecstatic smile.

“I can't help it,” said the prince. “Lavater would have said I
lack the bump of paternity.”

“Don't joke; I mean to have a serious talk with you. Do you know
I am dissatisfied with your younger son? Between ourselves” (and her
face assumed its melancholy expression), “he was mentioned at Her
Majesty's and you were pitied....”

The prince answered nothing, but she looked at him significantly,
awaiting a reply. He frowned.

“What would you have me do?” he said at last. “You know I did all
a father could for their education, and they have both turned out fools.
Hippolyte is at least a quiet fool, but Anatole is an active one. That
is the only difference between them.” He said this smiling in a way
more natural and animated than usual, so that the wrinkles round
his mouth very clearly revealed something unexpectedly coarse and
unpleasant.

“And why are children born to such men as you? If you were not a
father there would be nothing I could reproach you with,” said Anna
Pávlovna, looking up pensively.

“I am your faithful slave and to you alone I can confess that my
children are the bane of my life. It is the cross I have to bear. That
is how I explain it to myself. It can't be helped!”

He said no more, but expressed his resignation to cruel fate by a
gesture. Anna Pávlovna meditated.

“Have you never thought of marrying your prodigal son Anatole?” she
asked. “They say old maids have a mania for matchmaking, and though I
don't feel that weakness in myself as yet, I know a little person who
is very unhappy with her father. She is a relation of yours, Princess
Mary Bolkónskaya.”

Prince Vasíli did not reply, though, with the quickness of memory and
perception befitting a man of the world, he indicated by a movement of
the head that he was considering this information.

“Do you know,” he said at last, evidently unable to check the sad
current of his thoughts, “that Anatole is costing me forty thousand
rubles a year? And,” he went on after a pause, “what will it be in
five years, if he goes on like this?” Presently he added: “That's
what we fathers have to put up with.... Is this princess of yours
rich?”

“Her father is very rich and stingy. He lives in the country. He is
the well-known Prince Bolkónski who had to retire from the army under
the late Emperor, and was nicknamed 'the King of Prussia.' He is
very clever but eccentric, and a bore. The poor girl is very unhappy.
She has a brother; I think you know him, he married Lise Meinen lately.
He is an aide-de-camp of Kutúzov's and will be here tonight.”

“Listen, dear Annette,” said the prince, suddenly taking Anna
Pávlovna's hand and for some reason drawing it downwards. “Arrange
that affair for me and I shall always be your most devoted slave-slafe
with an f, as a village elder of mine writes in his reports. She is rich
and of good family and that's all I want.”

And with the familiarity and easy grace peculiar to him, he raised the
maid of honor's hand to his lips, kissed it, and swung it to and fro
as he lay back in his armchair, looking in another direction.

“Attendez,” said Anna Pávlovna, reflecting, “I'll speak to
Lise, young Bolkónski's wife, this very evening, and perhaps the
thing can be arranged. It shall be on your family's behalf that I'll
start my apprenticeship as old maid."""


@pytest.fixture(scope="module")
def server():
    config = LettaConfig.load()
    print("CONFIG PATH", config.config_path)

    config.save()

    server = SyncServer()
    return server


@pytest.fixture(scope="module")
def org_id(server):
    # create org
    org = server.organization_manager.create_default_organization()
    print(f"Created org\n{org.id}")

    yield org.id

    # cleanup
    with server.organization_manager.session_maker() as session:
        session.execute(delete(Step))
        session.execute(delete(Provider))
        session.commit()
    server.organization_manager.delete_organization_by_id(org.id)


@pytest.fixture(scope="module")
def user(server, org_id):
    user = server.user_manager.create_default_user()
    yield user
    server.user_manager.delete_user_by_id(user.id)


@pytest.fixture(scope="module")
def user_id(server, user):
    # create user
    yield user.id


@pytest.fixture(scope="module")
def base_tools(server, user_id):
    actor = server.user_manager.get_user_or_default(user_id)
    tools = []
    for tool_name in BASE_TOOLS:
        tools.append(server.tool_manager.get_tool_by_name(tool_name=tool_name, actor=actor))

    yield tools


@pytest.fixture(scope="module")
def base_memory_tools(server, user_id):
    actor = server.user_manager.get_user_or_default(user_id)
    tools = []
    for tool_name in BASE_MEMORY_TOOLS:
        tools.append(server.tool_manager.get_tool_by_name(tool_name=tool_name, actor=actor))

    yield tools


@pytest.fixture(scope="module")
def agent_id(server, user_id, base_tools):
    # create agent
    actor = server.user_manager.get_user_or_default(user_id)
    agent_state = server.create_agent(
        request=CreateAgent(
            name="test_agent",
            tool_ids=[t.id for t in base_tools],
            memory_blocks=[],
            model="openai/gpt-4o-mini",
            embedding="openai/text-embedding-ada-002",
        ),
        actor=actor,
    )
    print(f"Created agent\n{agent_state}")
    yield agent_state.id

    # cleanup
    server.agent_manager.delete_agent(agent_state.id, actor=actor)


@pytest.fixture(scope="module")
def other_agent_id(server, user_id, base_tools):
    # create agent
    actor = server.user_manager.get_user_or_default(user_id)
    agent_state = server.create_agent(
        request=CreateAgent(
            name="test_agent_other",
            tool_ids=[t.id for t in base_tools],
            memory_blocks=[],
            model="openai/gpt-4o-mini",
            embedding="openai/text-embedding-ada-002",
        ),
        actor=actor,
    )
    print(f"Created agent\n{agent_state}")
    yield agent_state.id

    # cleanup
    server.agent_manager.delete_agent(agent_state.id, actor=actor)


def test_error_on_nonexistent_agent(server, user, agent_id):
    try:
        fake_agent_id = str(uuid.uuid4())
        server.user_message(user_id=user.id, agent_id=fake_agent_id, message="Hello?")
        raise Exception("user_message call should have failed")
    except (KeyError, ValueError) as e:
        # Error is expected
        print(e)
    except:
        raise


@pytest.mark.order(1)
def test_user_message_memory(server, user, agent_id):
    try:
        server.user_message(user_id=user.id, agent_id=agent_id, message="/memory")
        raise Exception("user_message call should have failed")
    except ValueError as e:
        # Error is expected
        print(e)
    except:
        raise

    server.run_command(user_id=user.id, agent_id=agent_id, command="/memory")


@pytest.mark.order(3)
def test_load_data(server, user, agent_id):
    # create source
    passages_before = server.agent_manager.list_passages(actor=user, agent_id=agent_id, after=None, limit=10000)
    assert len(passages_before) == 0

    source = server.source_manager.create_source(
        PydanticSource(name="test_source", embedding_config=EmbeddingConfig.default_config(provider="openai")), actor=user
    )

    # load data
    archival_memories = [
        "alpha",
        "Cinderella wore a blue dress",
        "Dog eat dog",
        "ZZZ",
        "Shishir loves indian food",
    ]
    connector = DummyDataConnector(archival_memories)
    server.load_data(user.id, connector, source.name)

    # attach source
    server.agent_manager.attach_source(agent_id=agent_id, source_id=source.id, actor=user)

    # check archival memory size
    passages_after = server.agent_manager.list_passages(actor=user, agent_id=agent_id, after=None, limit=10000)
    assert len(passages_after) == 5


def test_save_archival_memory(server, user_id, agent_id):
    # TODO: insert into archival memory
    pass


@pytest.mark.order(4)
def test_user_message(server, user, agent_id):
    # add data into recall memory
    response = server.user_message(user_id=user.id, agent_id=agent_id, message="What's up?")
    assert response.step_count == 1
    assert response.completion_tokens > 0
    assert response.prompt_tokens > 0
    assert response.total_tokens > 0


@pytest.mark.order(5)
def test_get_recall_memory(server, org_id, user, agent_id):
    # test recall memory cursor pagination
    actor = user
    messages_1 = server.get_agent_recall(user_id=user.id, agent_id=agent_id, limit=2)
    cursor1 = messages_1[-1].id
    messages_2 = server.get_agent_recall(user_id=user.id, agent_id=agent_id, after=cursor1, limit=1000)
    messages_3 = server.get_agent_recall(user_id=user.id, agent_id=agent_id, limit=1000)
    messages_3[-1].id
    assert messages_3[-1].created_at >= messages_3[0].created_at
    assert len(messages_3) == len(messages_1) + len(messages_2)
    messages_4 = server.get_agent_recall(user_id=user.id, agent_id=agent_id, reverse=True, before=cursor1)
    assert len(messages_4) == 1

    # test in-context message ids
    in_context_ids = server.agent_manager.get_agent_by_id(agent_id=agent_id, actor=actor).message_ids

    message_ids = [m.id for m in messages_3]
    for message_id in in_context_ids:
        assert message_id in message_ids, f"{message_id} not in {message_ids}"


@pytest.mark.order(6)
def test_get_archival_memory(server, user, agent_id):
    # test archival memory cursor pagination
    actor = user

    # List latest 2 passages
    passages_1 = server.agent_manager.list_passages(
        actor=actor,
        agent_id=agent_id,
        ascending=False,
        limit=2,
    )
    assert len(passages_1) == 2, f"Returned {[p.text for p in passages_1]}, not equal to 2"

    # List next 3 passages (earliest 3)
    cursor1 = passages_1[-1].id
    passages_2 = server.agent_manager.list_passages(
        actor=actor,
        agent_id=agent_id,
        ascending=False,
        before=cursor1,
    )

    # List all 5
    cursor2 = passages_1[0].created_at
    passages_3 = server.agent_manager.list_passages(
        actor=actor,
        agent_id=agent_id,
        ascending=False,
        end_date=cursor2,
        limit=1000,
    )
    assert len(passages_2) in [3, 4]  # NOTE: exact size seems non-deterministic, so loosen test
    assert len(passages_3) in [4, 5]  # NOTE: exact size seems non-deterministic, so loosen test

    latest = passages_1[0]
    earliest = passages_2[-1]

    # test archival memory
    passage_1 = server.agent_manager.list_passages(actor=actor, agent_id=agent_id, limit=1, ascending=True)
    assert len(passage_1) == 1
    assert passage_1[0].text == "alpha"
    passage_2 = server.agent_manager.list_passages(actor=actor, agent_id=agent_id, after=earliest.id, limit=1000, ascending=True)
    assert len(passage_2) in [4, 5]  # NOTE: exact size seems non-deterministic, so loosen test
    assert all("alpha" not in passage.text for passage in passage_2)
    # test safe empty return
    passage_none = server.agent_manager.list_passages(actor=actor, agent_id=agent_id, after=latest.id, limit=1000, ascending=True)
    assert len(passage_none) == 0


def test_get_context_window_overview(server: SyncServer, user, agent_id):
    """Test that the context window overview fetch works"""
    overview = server.get_agent_context_window(agent_id=agent_id, actor=user)
    assert overview is not None

    # Run some basic checks
    assert overview.context_window_size_max is not None
    assert overview.context_window_size_current is not None
    assert overview.num_archival_memory is not None
    assert overview.num_recall_memory is not None
    assert overview.num_tokens_external_memory_summary is not None
    assert overview.external_memory_summary is not None
    assert overview.num_tokens_system is not None
    assert overview.system_prompt is not None
    assert overview.num_tokens_core_memory is not None
    assert overview.core_memory is not None
    assert overview.num_tokens_summary_memory is not None
    if overview.num_tokens_summary_memory > 0:
        assert overview.summary_memory is not None
    else:
        assert overview.summary_memory is None
    assert overview.num_tokens_functions_definitions is not None
    if overview.num_tokens_functions_definitions > 0:
        assert overview.functions_definitions is not None
    else:
        assert overview.functions_definitions is None
    assert overview.num_tokens_messages is not None
    assert overview.messages is not None

    assert overview.context_window_size_max >= overview.context_window_size_current
    assert overview.context_window_size_current == (
        overview.num_tokens_system
        + overview.num_tokens_core_memory
        + overview.num_tokens_summary_memory
        + overview.num_tokens_messages
        + overview.num_tokens_functions_definitions
        + overview.num_tokens_external_memory_summary
    )


def test_delete_agent_same_org(server: SyncServer, org_id: str, user: User):
    agent_state = server.create_agent(
        request=CreateAgent(
            name="nonexistent_tools_agent",
            memory_blocks=[],
            model="openai/gpt-4o-mini",
            embedding="openai/text-embedding-ada-002",
        ),
        actor=user,
    )

    # create another user in the same org
    another_user = server.user_manager.create_user(User(organization_id=org_id, name="another"))

    # test that another user in the same org can delete the agent
    server.agent_manager.delete_agent(agent_state.id, actor=another_user)


def test_read_local_llm_configs(server: SyncServer, user: User):
    configs_base_dir = os.path.join(os.path.expanduser("~"), ".letta", "llm_configs")
    clean_up_dir = False
    if not os.path.exists(configs_base_dir):
        os.makedirs(configs_base_dir)
        clean_up_dir = True

    try:
        sample_config = LLMConfig(
            model="my-custom-model",
            model_endpoint_type="openai",
            model_endpoint="https://api.openai.com/v1",
            context_window=8192,
            handle="caren/my-custom-model",
        )

        config_filename = f"custom_llm_config_{uuid.uuid4().hex}.json"
        config_filepath = os.path.join(configs_base_dir, config_filename)
        with open(config_filepath, "w") as f:
            json.dump(sample_config.model_dump(), f)

        # Call list_llm_models
        assert os.path.exists(configs_base_dir)
        llm_models = server.list_llm_models()

        # Assert that the config is in the returned models
        assert any(
            model.model == "my-custom-model"
            and model.model_endpoint_type == "openai"
            and model.model_endpoint == "https://api.openai.com/v1"
            and model.context_window == 8192
            and model.handle == "caren/my-custom-model"
            for model in llm_models
        ), "Custom LLM config not found in list_llm_models result"

        # Try to use in agent creation
        context_window_override = 4000
        agent = server.create_agent(
            request=CreateAgent(
                model="caren/my-custom-model",
                context_window_limit=context_window_override,
                embedding="openai/text-embedding-ada-002",
            ),
            actor=user,
        )
        assert agent.llm_config.model == sample_config.model
        assert agent.llm_config.model_endpoint == sample_config.model_endpoint
        assert agent.llm_config.model_endpoint_type == sample_config.model_endpoint_type
        assert agent.llm_config.context_window == context_window_override
        assert agent.llm_config.handle == sample_config.handle

    finally:
        os.remove(config_filepath)
        if clean_up_dir:
            shutil.rmtree(configs_base_dir)


def _test_get_messages_letta_format(
    server,
    user,
    agent_id,
    reverse=False,
):
    """Test mapping between messages and letta_messages with reverse=False."""

    messages = server.get_agent_recall(
        user_id=user.id,
        agent_id=agent_id,
        limit=1000,
        reverse=reverse,
        return_message_object=True,
        use_assistant_message=False,
    )
    assert all(isinstance(m, Message) for m in messages)

    letta_messages = server.get_agent_recall(
        user_id=user.id,
        agent_id=agent_id,
        limit=1000,
        reverse=reverse,
        return_message_object=False,
        use_assistant_message=False,
    )
    assert all(isinstance(m, LettaMessage) for m in letta_messages)

    print(f"Messages: {len(messages)}, LettaMessages: {len(letta_messages)}")

    letta_message_index = 0
    for i, message in enumerate(messages):
        assert isinstance(message, Message)

        # Defensive bounds check for letta_messages
        if letta_message_index >= len(letta_messages):
            print(f"Error: letta_message_index out of range. Expected more letta_messages for message {i}: {message.role}")
            raise ValueError(f"Mismatch in letta_messages length. Index: {letta_message_index}, Length: {len(letta_messages)}")

        print(f"Processing message {i}: {message.role}, {message.text[:50] if message.text else 'null'}")
        while letta_message_index < len(letta_messages):
            letta_message = letta_messages[letta_message_index]

            # Validate mappings for assistant role
            if message.role == MessageRole.assistant:
                print(f"Assistant Message at {i}: {type(letta_message)}")

                if reverse:
                    # Reverse handling: ToolCallMessage come first
                    if message.tool_calls:
                        for tool_call in message.tool_calls:
                            try:
                                json.loads(tool_call.function.arguments)
                            except json.JSONDecodeError:
                                warnings.warn(f"Invalid JSON in function arguments: {tool_call.function.arguments}")
                            assert isinstance(letta_message, ToolCallMessage)
                            letta_message_index += 1
                            if letta_message_index >= len(letta_messages):
                                break
                            letta_message = letta_messages[letta_message_index]

                    if message.text:
                        assert isinstance(letta_message, ReasoningMessage)
                        letta_message_index += 1
                    else:
                        assert message.tool_calls is not None

                else:  # Non-reverse handling
                    if message.text:
                        assert isinstance(letta_message, ReasoningMessage)
                        letta_message_index += 1
                        if letta_message_index >= len(letta_messages):
                            break
                        letta_message = letta_messages[letta_message_index]

                    if message.tool_calls:
                        for tool_call in message.tool_calls:
                            try:
                                json.loads(tool_call.function.arguments)
                            except json.JSONDecodeError:
                                warnings.warn(f"Invalid JSON in function arguments: {tool_call.function.arguments}")
                            assert isinstance(letta_message, ToolCallMessage)
                            assert tool_call.function.name == letta_message.tool_call.name
                            assert tool_call.function.arguments == letta_message.tool_call.arguments
                            letta_message_index += 1
                            if letta_message_index >= len(letta_messages):
                                break
                            letta_message = letta_messages[letta_message_index]

            elif message.role == MessageRole.user:
                assert isinstance(letta_message, UserMessage)
                assert unpack_message(message.text) == letta_message.content
                letta_message_index += 1

            elif message.role == MessageRole.system:
                assert isinstance(letta_message, SystemMessage)
                assert message.text == letta_message.content
                letta_message_index += 1

            elif message.role == MessageRole.tool:
                assert isinstance(letta_message, ToolReturnMessage)
                assert message.text == letta_message.tool_return
                letta_message_index += 1

            else:
                raise ValueError(f"Unexpected message role: {message.role}")

            break  # Exit the letta_messages loop after processing one mapping

    if letta_message_index < len(letta_messages):
        warnings.warn(f"Extra letta_messages found: {len(letta_messages) - letta_message_index}")


def test_get_messages_letta_format(server, user, agent_id):
    # for reverse in [False, True]:
    for reverse in [False]:
        _test_get_messages_letta_format(server, user, agent_id, reverse=reverse)


EXAMPLE_TOOL_SOURCE = '''
def ingest(message: str):
    """
    Ingest a message into the system.

    Args:
        message (str): The message to ingest into the system.

    Returns:
        str: The result of ingesting the message.
    """
    return f"Ingested message {message}"

'''

EXAMPLE_TOOL_SOURCE_WITH_ENV_VAR = '''
def ingest():
    """
    Ingest a message into the system.

    Returns:
        str: The result of ingesting the message.
    """
    import os
    return os.getenv("secret")
'''


EXAMPLE_TOOL_SOURCE_WITH_DISTRACTOR = '''
def util_do_nothing():
    """
    A util function that does nothing.

    Returns:
        str: Dummy output.
    """
    print("I'm a distractor")

def ingest(message: str):
    """
    Ingest a message into the system.

    Args:
        message (str): The message to ingest into the system.

    Returns:
        str: The result of ingesting the message.
    """
    util_do_nothing()
    return f"Ingested message {message}"

'''


def test_tool_run(server, mock_e2b_api_key_none, user, agent_id):
    """Test that the server can run tools"""

    result = server.run_tool_from_source(
        actor=user,
        tool_source=EXAMPLE_TOOL_SOURCE,
        tool_source_type="python",
        tool_args={"message": "Hello, world!"},
        # tool_name="ingest",
    )
    print(result)
    assert result.status == "success"
    assert result.tool_return == "Ingested message Hello, world!", result.tool_return
    assert not result.stdout
    assert not result.stderr

    result = server.run_tool_from_source(
        actor=user,
        tool_source=EXAMPLE_TOOL_SOURCE_WITH_ENV_VAR,
        tool_source_type="python",
        tool_args={},
        tool_env_vars={"secret": "banana"},
    )
    print(result)
    assert result.status == "success"
    assert result.tool_return == "banana", result.tool_return
    assert not result.stdout
    assert not result.stderr

    result = server.run_tool_from_source(
        actor=user,
        tool_source=EXAMPLE_TOOL_SOURCE,
        tool_source_type="python",
        tool_args={"message": "Well well well"},
        # tool_name="ingest",
    )
    print(result)
    assert result.status == "success"
    assert result.tool_return == "Ingested message Well well well", result.tool_return
    assert not result.stdout
    assert not result.stderr

    result = server.run_tool_from_source(
        actor=user,
        tool_source=EXAMPLE_TOOL_SOURCE,
        tool_source_type="python",
        tool_args={"bad_arg": "oh no"},
        # tool_name="ingest",
    )
    print(result)
    assert result.status == "error"
    assert "Error" in result.tool_return, result.tool_return
    assert "missing 1 required positional argument" in result.tool_return, result.tool_return
    assert not result.stdout
    assert result.stderr
    assert "missing 1 required positional argument" in result.stderr[0]

    # Test that we can still pull the tool out by default (pulls that last tool in the source)
    result = server.run_tool_from_source(
        actor=user,
        tool_source=EXAMPLE_TOOL_SOURCE_WITH_DISTRACTOR,
        tool_source_type="python",
        tool_args={"message": "Well well well"},
        # tool_name="ingest",
    )
    print(result)
    assert result.status == "success"
    assert result.tool_return == "Ingested message Well well well", result.tool_return
    assert result.stdout
    assert "I'm a distractor" in result.stdout[0]
    assert not result.stderr

    # Test that we can pull the tool out by name
    result = server.run_tool_from_source(
        actor=user,
        tool_source=EXAMPLE_TOOL_SOURCE_WITH_DISTRACTOR,
        tool_source_type="python",
        tool_args={"message": "Well well well"},
        tool_name="ingest",
    )
    print(result)
    assert result.status == "success"
    assert result.tool_return == "Ingested message Well well well", result.tool_return
    assert result.stdout
    assert "I'm a distractor" in result.stdout[0]
    assert not result.stderr

    # Test that we can pull a different tool out by name
    result = server.run_tool_from_source(
        actor=user,
        tool_source=EXAMPLE_TOOL_SOURCE_WITH_DISTRACTOR,
        tool_source_type="python",
        tool_args={},
        tool_name="util_do_nothing",
    )
    print(result)
    assert result.status == "success"
    assert result.tool_return == str(None), result.tool_return
    assert result.stdout
    assert "I'm a distractor" in result.stdout[0]
    assert not result.stderr


def test_composio_client_simple(server):
    apps = server.get_composio_apps()
    # Assert there's some amount of apps returned
    assert len(apps) > 0

    app = apps[0]
    actions = server.get_composio_actions_from_app_name(composio_app_name=app.name)

    # Assert there's some amount of actions
    assert len(actions) > 0


def test_memory_rebuild_count(server, user, mock_e2b_api_key_none, base_tools, base_memory_tools):
    """Test that the memory rebuild is generating the correct number of role=system messages"""
    actor = user
    # create agent
    agent_state = server.create_agent(
        request=CreateAgent(
            name="test_memory_rebuild_count",
            tool_ids=[t.id for t in base_tools + base_memory_tools],
            memory_blocks=[
                CreateBlock(label="human", value="The human's name is Bob."),
                CreateBlock(label="persona", value="My name is Alice."),
            ],
            model="openai/gpt-4o-mini",
            embedding="openai/text-embedding-ada-002",
        ),
        actor=actor,
    )
    print(f"Created agent\n{agent_state}")

    def count_system_messages_in_recall() -> Tuple[int, List[LettaMessage]]:

        # At this stage, there should only be 1 system message inside of recall storage
        letta_messages = server.get_agent_recall(
            user_id=user.id,
            agent_id=agent_state.id,
            limit=1000,
            # reverse=reverse,
            return_message_object=False,
        )
        assert all(isinstance(m, LettaMessage) for m in letta_messages)

        print("LETTA_MESSAGES:")
        for i, m in enumerate(letta_messages):
            print(f"{i}: {type(m)} ...{str(m)[-50:]}")

        # Collect system messages and their texts
        system_messages = [m for m in letta_messages if m.message_type == "system_message"]
        return len(system_messages), letta_messages

    try:
        # At this stage, there should only be 1 system message inside of recall storage
        num_system_messages, all_messages = count_system_messages_in_recall()
        assert num_system_messages == 1, (num_system_messages, all_messages)

        # Run server.load_agent, and make sure that the number of system messages is still 2
        server.load_agent(agent_id=agent_state.id, actor=actor)

        num_system_messages, all_messages = count_system_messages_in_recall()
        assert num_system_messages == 1, (num_system_messages, all_messages)

    finally:
        # cleanup
        server.agent_manager.delete_agent(agent_state.id, actor=actor)


def test_load_file_to_source(server: SyncServer, user_id: str, agent_id: str, other_agent_id: str, tmp_path):
    actor = server.user_manager.get_user_or_default(user_id)

    existing_sources = server.source_manager.list_sources(actor=actor)
    if len(existing_sources) > 0:
        for source in existing_sources:
            server.agent_manager.detach_source(agent_id=agent_id, source_id=source.id, actor=actor)
    initial_passage_count = server.agent_manager.passage_size(agent_id=agent_id, actor=actor)
    assert initial_passage_count == 0

    # Create a source
    source = server.source_manager.create_source(
        PydanticSource(
            name="timber_source",
            embedding_config=EmbeddingConfig.default_config(provider="openai"),
            created_by_id=user_id,
        ),
        actor=actor,
    )
    assert source.created_by_id == user_id

    # Create a test file with some content
    test_file = tmp_path / "test.txt"
    test_content = "We have a dog called Timber. He likes to sleep and eat chicken."
    test_file.write_text(test_content)

    # Attach source to agent first
    server.agent_manager.attach_source(agent_id=agent_id, source_id=source.id, actor=actor)

    # Create a job for loading the first file
    job = server.job_manager.create_job(
        PydanticJob(
            user_id=user_id,
            metadata={"type": "embedding", "filename": test_file.name, "source_id": source.id},
        ),
        actor=actor,
    )

    # Load the first file to source
    server.load_file_to_source(
        source_id=source.id,
        file_path=str(test_file),
        job_id=job.id,
        actor=actor,
    )

    # Verify job completed successfully
    job = server.job_manager.get_job_by_id(job_id=job.id, actor=actor)
    assert job.status == "completed"
    assert job.metadata["num_passages"] == 1
    assert job.metadata["num_documents"] == 1

    # Verify passages were added
    first_file_passage_count = server.agent_manager.passage_size(agent_id=agent_id, actor=actor)
    assert first_file_passage_count > initial_passage_count

    # Create a second test file with different content
    test_file2 = tmp_path / "test2.txt"
    test_file2.write_text(WAR_AND_PEACE)

    # Create a job for loading the second file
    job2 = server.job_manager.create_job(
        PydanticJob(
            user_id=user_id,
            metadata={"type": "embedding", "filename": test_file2.name, "source_id": source.id},
        ),
        actor=actor,
    )

    # Load the second file to source
    server.load_file_to_source(
        source_id=source.id,
        file_path=str(test_file2),
        job_id=job2.id,
        actor=actor,
    )

    # Verify second job completed successfully
    job2 = server.job_manager.get_job_by_id(job_id=job2.id, actor=actor)
    assert job2.status == "completed"
    assert job2.metadata["num_passages"] >= 10
    assert job2.metadata["num_documents"] == 1

    # Verify passages were appended (not replaced)
    final_passage_count = server.agent_manager.passage_size(agent_id=agent_id, actor=actor)
    assert final_passage_count > first_file_passage_count

    # Verify both old and new content is searchable
    passages = server.agent_manager.list_passages(
        agent_id=agent_id,
        actor=actor,
        query_text="what does Timber like to eat",
        embedding_config=EmbeddingConfig.default_config(provider="openai"),
        embed_query=True,
    )
    assert len(passages) == final_passage_count
    assert any("chicken" in passage.text.lower() for passage in passages)
    assert any("Anna".lower() in passage.text.lower() for passage in passages)

    # Initially should have no passages
    initial_agent2_passages = server.agent_manager.passage_size(agent_id=other_agent_id, actor=actor, source_id=source.id)
    assert initial_agent2_passages == 0

    # Attach source to second agent
    server.agent_manager.attach_source(agent_id=other_agent_id, source_id=source.id, actor=actor)

    # Verify second agent has same number of passages as first agent
    agent2_passages = server.agent_manager.passage_size(agent_id=other_agent_id, actor=actor, source_id=source.id)
    agent1_passages = server.agent_manager.passage_size(agent_id=agent_id, actor=actor, source_id=source.id)
    assert agent2_passages == agent1_passages

    # Verify second agent can query the same content
    passages2 = server.agent_manager.list_passages(
        actor=actor,
        agent_id=other_agent_id,
        source_id=source.id,
        query_text="what does Timber like to eat",
        embedding_config=EmbeddingConfig.default_config(provider="openai"),
        embed_query=True,
    )
    assert len(passages2) == len(passages)
    assert any("chicken" in passage.text.lower() for passage in passages2)
    assert any("Anna".lower() in passage.text.lower() for passage in passages2)


def test_add_remove_tools_update_agent(server: SyncServer, user_id: str, base_tools):
    """Test that the memory rebuild is generating the correct number of role=system messages"""
    actor = server.user_manager.get_user_or_default(user_id)

    # create agent
    agent_state = server.create_agent(
        request=CreateAgent(
            name="memory_rebuild_test_agent",
            tool_ids=[],
            memory_blocks=[
                CreateBlock(label="human", value="The human's name is Bob."),
                CreateBlock(label="persona", value="My name is Alice."),
            ],
            model="openai/gpt-4o-mini",
            embedding="openai/text-embedding-ada-002",
            include_base_tools=False,
        ),
        actor=actor,
    )
    assert len(agent_state.tools) == 0

    # base update agent call
    request = UpdateAgent(
        **{
            "system": "You are Letta, the latest version of Limnal Corporation's digital companion, developed in 2023.\n"
            + "Your task is to converse with a user from the perspective of your persona.\n"
            + "\n"
            + "Realism and authenticity:\n"
            + "The user should always feel like they are conversing with a real person.\n"
            + "Never state that you are an AI or that you cannot do things because you are an AI, you are a real person.\n"
            + "Do not discuss Limnal Corp. with the user, you do not know anything about Limnal Corp.\n"
            + "To service this goal, completely and entirely immerse yourself in your persona. You are your persona.\n"
            + "Think like them, act like them, talk like them.\n"
            + "If your persona details include example dialogue, follow it! Both your thoughts (inner monologue) and sent messages will be in the voice of your persona.\n"
            + "Never use generic phrases like 'How can I assist you today?', they have a strong negative association with older generation AIs.\n"
            + "\n"
            + "Control flow:\n"
            + "Unlike a human, your b"
            + "Base instructions finished.\n"
            + "From now on, you are going to act as your persona.",
            "name": "name-d31d6a12-48af-4f71-9e9c-f4cec4731c40",
            "embedding_config": {
                "embedding_endpoint_type": "openai",
                "embedding_endpoint": "https://api.openai.com/v1",
                "embedding_model": "text-embedding-ada-002",
                "embedding_dim": 1536,
                "embedding_chunk_size": 300,
                "azure_endpoint": None,
                "azure_version": None,
                "azure_deployment": None,
            },
            "llm_config": {
                "model": "gpt-4",
                "model_endpoint_type": "openai",
                "model_endpoint": "https://api.openai.com/v1",
                "model_wrapper": None,
                "context_window": 8192,
                "put_inner_thoughts_in_kwargs": False,
            },
        }
    )

    # Add all the base tools
    request.tool_ids = [b.id for b in base_tools]
    agent_state = server.agent_manager.update_agent(agent_state.id, agent_update=request, actor=actor)
    assert len(agent_state.tools) == len(base_tools)

    # Remove one base tool
    request.tool_ids = [b.id for b in base_tools[:-2]]
    agent_state = server.agent_manager.update_agent(agent_state.id, agent_update=request, actor=actor)
    assert len(agent_state.tools) == len(base_tools) - 2


def test_messages_with_provider_override(server: SyncServer, user_id: str):
    actor = server.user_manager.get_user_or_default(user_id)
    provider = server.provider_manager.create_provider(
        provider=PydanticProvider(
            name="anthropic",
            api_key=os.getenv("ANTHROPIC_API_KEY"),
        ),
        actor=actor,
    )
    agent = server.create_agent(
        request=CreateAgent(
            memory_blocks=[],
            model="anthropic/claude-3-opus-20240229",
            context_window_limit=200000,
            embedding="openai/text-embedding-ada-002",
        ),
        actor=actor,
    )

    existing_messages = server.message_manager.list_messages_for_agent(agent_id=agent.id, actor=actor)

    usage = server.user_message(user_id=actor.id, agent_id=agent.id, message="Test message")
    assert usage, "Sending message failed"

    get_messages_response = server.message_manager.list_messages_for_agent(agent_id=agent.id, actor=actor, after=existing_messages[-1].id)
    assert len(get_messages_response) > 0, "Retrieving messages failed"

    step_ids = set([msg.step_id for msg in get_messages_response])
    completion_tokens, prompt_tokens, total_tokens = 0, 0, 0
    for step_id in step_ids:
        step = server.step_manager.get_step(step_id=step_id, actor=actor)
        assert step, "Step was not logged correctly"
        assert step.provider_id == provider.id
        assert step.provider_name == agent.llm_config.model_endpoint_type
        assert step.model == agent.llm_config.model
        assert step.context_window_limit == agent.llm_config.context_window
        completion_tokens += int(step.completion_tokens)
        prompt_tokens += int(step.prompt_tokens)
        total_tokens += int(step.total_tokens)

    assert completion_tokens == usage.completion_tokens
    assert prompt_tokens == usage.prompt_tokens
    assert total_tokens == usage.total_tokens

    server.provider_manager.delete_provider_by_id(provider.id, actor=actor)

    existing_messages = server.message_manager.list_messages_for_agent(agent_id=agent.id, actor=actor)

    usage = server.user_message(user_id=actor.id, agent_id=agent.id, message="Test message")
    assert usage, "Sending message failed"

    get_messages_response = server.message_manager.list_messages_for_agent(agent_id=agent.id, actor=actor, after=existing_messages[-1].id)
    assert len(get_messages_response) > 0, "Retrieving messages failed"

    step_ids = set([msg.step_id for msg in get_messages_response])
    completion_tokens, prompt_tokens, total_tokens = 0, 0, 0
    for step_id in step_ids:
        step = server.step_manager.get_step(step_id=step_id, actor=actor)
        assert step, "Step was not logged correctly"
        assert step.provider_id == None
        assert step.provider_name == agent.llm_config.model_endpoint_type
        assert step.model == agent.llm_config.model
        assert step.context_window_limit == agent.llm_config.context_window
        completion_tokens += int(step.completion_tokens)
        prompt_tokens += int(step.prompt_tokens)
        total_tokens += int(step.total_tokens)

    assert completion_tokens == usage.completion_tokens
    assert prompt_tokens == usage.prompt_tokens
    assert total_tokens == usage.total_tokens


def test_unique_handles_for_provider_configs(server: SyncServer):
    models = server.list_llm_models()
    model_handles = [model.handle for model in models]
    assert sorted(model_handles) == sorted(list(set(model_handles))), "All models should have unique handles"
    embeddings = server.list_embedding_models()
    embedding_handles = [embedding.handle for embedding in embeddings]
    assert sorted(embedding_handles) == sorted(list(set(embedding_handles))), "All embeddings should have unique handles"
