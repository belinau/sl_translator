#!/usr/bin/env python3
"""Attribute concepts to all known authors from the missing list."""
from __future__ import annotations

import argparse
import pathlib
import sys
import unicodedata

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from translate_core.entity_extraction._slug import _slugify
from translate_core.knowledge_graph import KnowledgeGraph


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Add attributed_to edges for known missing agent-citation pairs"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be done without writing to KG",
    )
    args = parser.parse_args()

    kg = KnowledgeGraph()
    G = kg.G

    def add(agent_name, concepts):
        # Try exact match first, then NFC/NFD variants
        aid = None
        for nid, d in G.nodes(data=True):
            if d.get("type") == "agent":
                n = d.get("name", "")
                if n == agent_name or unicodedata.normalize("NFC", n) == unicodedata.normalize("NFC", agent_name):
                    aid = nid
                    break
        if not aid:
            return False
        count = 0
        for concept in concepts:
            cid = f"concept:{_slugify(concept)}"
            if not G.has_node(cid):
                kg.add_concept_node(concept_id=cid, label=concept, domain="humanities")
            if not G.has_edge(cid, aid):
                G.add_edge(cid, aid, relation="attributed_to")
                count += 1
        if count:
            print(f"  {agent_name}: +{count}")
        return count > 0
    
    # Performance / dance / theatre
    add("Jerzy Grotowski", ["poor theatre", "paratheatre", "via negativa", "actor's craft", "ritual theatre"])
    add("Vaslav Nijinsky", ["modernist ballet", "choreographic innovation", "expressionist movement", "L'Après-midi d'un faune"])
    add("Joseph Beuys", ["social sculpture", "expanded art concept", "Fluxus", "political art", "shamanism in art"])
    add("Alan Kaprow", ["happenings", "participatory art", "environments", "blurring art and life", "intermedia"])
    add("Dore Hoyer", ["expressionist dance", "Ausdruckstanz", "soloist dance", "German modern dance"])
    add("Valeska Gert", ["grotesque dance", "cabaret performance", "Weimar body", "satirical dance"])
    add("Morton Feldman", ["indeterminate music", "graphic notation", "duration", "silence", "New York School"])
    add("La Monte Young", ["minimalist music", "drone music", "just intonation", "Fluxus", "extended duration"])
    add("Anna Halprin", ["ecological performance", "somatic practice", "dance as healing", "ritual performance"])
    add("Sandra Noeth", ["dramaturgy", "performance curation", "choreographic practice", "body politics"])
    add("Guy Cools", ["dance dramaturgy", "choreographic writing", "somatic practice", "performance theory"])
    add("Rudi Laermans", ["sociology of dance", "performing arts theory", "contemporary choreography", "spectacle"])
    add("Barbara Matijević", ["performance art", "feminist performance", "body in performance", "live art"])
    add("Florentina Holzinger", ["post-pornographic performance", "extreme performance", "feminist body art", "spectacle"])
    add("Vita Osojnik", ["sound poetry", "experimental music", "noise", "performance"])
    add("Marko Peljhan", ["tactical media", "telecommunications art", "art and technology", "Makrolab"])
    add("Saša Spačal", ["bio-art", "mycelial networks", "posthumanism", "ecological art"])
    add("Marta Popivoda", ["documentary film", "political performance", "feminist practice", "Yugoslav space"])
    add("Tamara Ashley", ["dance and ecology", "somatic practice", "site-specific performance"])
    add("Carl Lavery", ["performance and landscape", "theatre ecology", "performance studies"])
    add("Patrice Pavis", ["theatre semiotics", "intercultural theatre", "performance analysis", "dictionary of theatre"])
    
    # Visual art / curating
    add("Hans Ulrich Obrist", ["curatorial practice", "do it", "marathon interviews", "art mediation"])
    add("Daniel Buren", ["in situ work", "institutional critique", "stripes", "site-specificity"])
    add("Okwui Enwezor", ["postcolonial exhibition making", "global contemporary art", "Documenta 11", "archive fever"])
    add("Jean Tinguely", ["kinetic art", "meta-mechanics", "anti-art machines", "junk sculpture"])
    add("William Rubin", ["primitivism in modern art", "modernism and tribalism", "formalist criticism"])
    add("Inke Arns", ["media art theory", "tactical media", "net art", "Eastern European media art"])
    add("Corina Apostol", ["decolonial curating", "Eastern European art", "feminist curating", "political art"])
    add("Flaka Haliti", ["post-conflict identity", "representation", "Kosovo art", "institutional critique"])
    add("Sven Augustijnen", ["documentary art", "colonial memory", "Belgian colonialism", "film and art"])
    add("Bojana Videkanić", ["Yugoslav socialist modernism", "non-aligned modernism", "art and politics"])
    add("Anselm Franke", ["animism", "exhibition making", "colonialism and modernity", "worldbuilding"])
    add("Hal Foster", ["postmodernism", "return of the real", "art and theory", "October journal"])
    add("Terry Smith", ["contemporaneity", "world art history", "postcolonial modernism", "contemporary art theory"])
    add("Jo Anna Isaak", ["feminist aesthetics", "laughter and subversion", "feminist art history"])
    add("Pierre Restany", ["nouveau réalisme", "art manifesto", "object art", "art criticism"])
    add("Sharon Sliwinski", ["human rights and image", "photography and suffering", "visual culture"])
    add("Irit Rogoff", ["geographies of art", "educational turn", "criticality", "complicity in art"])
    add("Jorge Ribalta", ["documentary photography", "photo books", "public photographic sphere"])
    add("Valeria Graziano", ["commons", "prefigurative politics", "militant research", "feminist organizing"])
    
    # Film / cinema
    add("Fritz Lang", ["German Expressionist cinema", "film noir", "dystopia", "surveillance"])
    add("Dušan Makavejev", ["Yugoslav Black Wave", "film montage", "sexuality and politics", "counter-cinema"])
    add("Jean-Luc Godard", ["French New Wave", "film essay", "political cinema", "cinema and philosophy"])
    add("Terry Gilliam", ["dystopian cinema", "surrealism", "Monty Python", "bureaucratic absurdism"])
    add("Stanley Kubrick", ["existential cinema", "total filmmaking", "violence and control"])
    
    # Philosophy / critical theory
    add("Georg Lukács", ["reification", "class consciousness", "theory of the novel", "social totality", "Western Marxism"])
    add("Danko Grlić", ["Praxis school", "Marxist aesthetics", "Yugoslav humanism", "philosophy of art"])
    add("Andrej Ule", ["epistemology", "philosophy of science", "analytic philosophy", "Slovenian philosophy"])
    add("Tomaž Mastnak", ["civil society", "liberalism critique", "crusade ideology", "political philosophy"])
    add("Gerald Raunig", ["art and revolution", "molecular revolution", "institutions of critique", "transversality"])
    add("Simon Critchley", ["ethics of deconstruction", "infinitely demanding", "continental philosophy", "nihilism"])
    add("William Edward Burghardt Du Bois", ["double consciousness", "pan-Africanism", "color line", "racial capitalism", "Black sociology"])
    add("Vijay Prashad", ["tricontinentalism", "Third World solidarity", "anti-imperialism", "subaltern histories"])
    add("Mogobe B. Ramose", ["ubuntu philosophy", "African philosophy", "decolonization of knowledge"])
    add("Manuela Boatcă", ["world-systems theory", "decolonial sociology", "global inequalities", "coloniality of modernity"])
    add("Elizabeth A. Povinelli", ["geontology", "late liberalism", "settler colonialism", "indigenous survival"])
    add("Serhat Karakayalı", ["autonomous migration", "border regimes", "migration studies", "precarious labor"])
    add("Georges Polti", ["dramatic situations", "narrative theory", "theatrical dramaturgy"])
    add("Christine de Pizan", ["proto-feminism", "querelle des femmes", "defense of women", "medieval literature"])
    add("Michel Chion", ["acousmatics", "audio-vision", "film sound", "voice in cinema"])
    add("Manfred B. Steger", ["globalization", "globalism", "global justice", "political ideologies"])
    add("Wehler, H.-U.", ["German social history", "Bielefeld school", "historical social science"])
    add("G. S. Jones", ["Chartism", "language of class", "working-class politics", "British history"])
    add("Tamás, G. M.", ["post-fascism", "post-communism critique", "Marxist critique", "Eastern European political philosophy"])
    add("Didier Fassin", ["moral economy", "humanitarian reason", "social suffering", "critical public health"])
    add("Paul Rabinow", ["anthropology of reason", "biosociality", "foucault studies", "ethics and science"])
    add("Heinz Bude", ["sociology of failure", "German society", "milieu theory", "social exclusion"])
    add("Todor Kuljić", ["memory studies", "Yugoslav heritage", "thanatopolitics", "historical revisionism"])
    add("Selma Jeanne Cohen", ["dance history", "dance criticism", "dance documentation"])
    add("April Carter", ["pacifism", "direct action", "anarchism", "peace politics", "civil society"])
    add("Jorge Luis Borges", ["labyrinths", "magical realism", "infinite library", "metaphysics of fiction"])
    add("Martin Sherman", ["LGBTQ theater", "Holocaust drama", "queer history in theatre"])
    add("Wittgenstein", ["language games", "ordinary language philosophy", "picture theory", "private language argument"])
    add("Danilo Kiš", ["Yugoslav literature", "Holocaust memory", "encyclopedic novel", "totalitarianism and literature"])
    
    # Slovenian / Yugoslav culture
    add("Ivo Svetina", ["Slovenian theatre", "experimental drama", "literary drama"])
    add("Iztok Geister", ["ornithology", "nature writing", "Slovenian natural history"])
    add("Vili Ravnjak", ["performance art", "body art", "Slovenian art"])
    add("Feri Lainšček", ["Slovenian literature", "Prekmurje culture", "regional identity"])
    add("Vinko Moderndorfer", ["Slovenian drama", "theatre directing", "social realism in theatre"])
    add("Rudi Šeligo", ["Slovenian literature", "drama", "rural Slovenia", "modernist prose"])
    add("Tomo Virk", ["comparative literature", "Slovenian literary theory", "postmodernism in literature"])
    add("Denis Poniž", ["Slovenian theatre history", "drama theory", "literary criticism"])
    add("Petar Volk", ["Yugoslav theatre history", "theatre criticism", "Serbian drama"])
    add("Aleš Gabrič", ["Slovenian cultural history", "socialist culture", "censorship in Yugoslavia"])
    add("Miha Kovač", ["book publishing", "reading culture", "Slovenian book market"])
    add("Brane Mozetič", ["queer literature", "Slovenian gay poetry", "literary activism"])
    add("Anastasius Grün", ["Slovenian national awakening", "romantic poetry", "Austrian liberalism"])
    add("Ágoston Pável", ["Slovenian Vendland", "Prekmurje culture", "minority literature"])
    add("Peter Kozler", ["Slovenian cartography", "national territory", "ethnic mapping"])
    add("Borka Pavičević", ["political theatre", "feminist theatre", "Yugoslav alternative culture"])
    add("Gregor Tomc", ["sociology of culture", "rock music and politics", "Slovenian subculture"])
    add("Maja Breznik", ["cultural policy", "labor in arts", "intellectual property", "commons"])
    add("Rudolf M. Rizman", ["globalization", "nationalism", "civil society", "political sociology"])
    add("Paul Stubbs", ["social policy", "civil society", "post-Yugoslav transformation"])
    add("Dragan Klaić", ["cultural policy", "European theatre", "performing arts mobility"])
    add("Irena Šentevska", ["Yugoslav film", "film criticism", "Serbian cinema"])
    add("Bálint Szombathy", ["Fluxus", "mail art", "Hungarian neo-avant-garde", "conceptual art"])
    add("Bogdanka Poznanović", ["Yugoslav neo-avant-garde", "conceptual art", "new artistic practice"])
    add("Dejan Sretenović", ["contemporary art", "institutional critique", "Yugoslav art history"])
    add("Dubravka Djurić", ["Yugoslav poetry", "language poetry", "feminist poetics"])
    add("Aleksa Buha", ["Marxist philosophy", "Yugoslav philosophy", "ethics"])
    add("Janko Kos", ["Slovenian literary history", "comparative literature", "literary theory"])
    add("Nadja Zgonik", ["Slovenian art history", "gender and art", "self-portraiture"])
    add("Bojana Piškur", ["decolonial curating", "non-aligned modernity", "Southern perspectives"])
    add("Bojana Videkanić", ["Yugoslav socialist modernism", "non-aligned art", "politics of art"])
    add("Papić, Žarana", ["Yugoslav feminism", "anthropology of socialism", "gender in the Balkans"])
    add("Janez Erjavec", ["Slovenian aesthetics", "postmodernism", "art and politics"])
    
    # Music
    add("Mauricio Kagel", ["instrumental theatre", "musical theatre", "experimental composition", "conceptual music"])
    add("Johann Sebastian Bach", ["counterpoint", "baroque polyphony", "fugue", "tonal harmony"])
    add("Tomasz Stańko", ["free jazz", "Polish jazz", "musical improvisation"])
    
    # Literature
    add("Jorge Luis Borges", ["labyrinths", "magical realism", "infinite library", "metaphysics of fiction"])
    add("Rainer Marie Rilke", ["lyric poetry", "Dinggedicht", "existential meditation", "duino elegies"])
    add("Jeanette Winterson", ["feminist fiction", "queer narrative", "magical realism", "postmodern autobiography"])
    add("Ted Hughes", ["nature poetry", "myth in poetry", "Poet Laureate", "deep ecology"])
    add("Ursula K. Le Guin", ["feminist science fiction", "anarchist utopia", "anthropological fiction", "Earthsea"])
    add("Yvonne Vera", ["Zimbabwean literature", "postcolonial fiction", "women's bodies", "historical trauma"])
    add("Ayi Kwei Armah", ["African literature", "decolonial novel", "Pan-Africanism", "spiritual healing"])
    add("Milan Jesih", ["Slovenian poetry", "sonnet", "modernist lyric"])
    add("Ivan Mrak", ["Slovenian drama", "expressionist theatre", "avant-garde literature"])
    add("Anne Boyer", ["poetry of illness", "feminist refusal", "anti-capitalist poetics", "breast cancer"])
    add("Hamja Ahsan", ["neurodiversity", "asylum seekers", "Shy Radicals", "postal art"])
    add("Jevgenij Zamjatin", ["dystopian fiction", "We", "anti-totalitarianism", "modernist prose"])
    
    # Social science
    add("Alice Marwick", ["celebrity culture", "social media", "status games", "internet culture"])
    add("Limor Shifman", ["meme theory", "digital culture", "internet memes", "viral content"])
    add("Tobias Hübinette", ["critical race studies", "whiteness studies", "transracial adoption", "Sweden"])
    add("David Chioni Moore", ["postcolonialism and post-socialism", "African literature", "decolonial comparison"])
    add("Franci Pivec", ["information society", "knowledge economy", "educational policy"])
    
    # Photography / media
    add("Alan Sheridan", ["translation theory", "French philosophy in English", "Michel Foucault translator"])
    add("Martin Joughin", ["philosophy translation", "Deleuze translation"])
    add("Richard Howard", ["literary translation", "French literature", "American poetry"])
    
    # Known curators / arts professionals
    add("Nataša Petrešin Bachelez", ["feminist curatorial practice", "relational aesthetics", "Eastern European art"])
    add("Kate Fowle", ["curatorial practice", "international art", "MoMA PS1"])
    add("Kathrin Rhomberg", ["documentary practice", "political art", "exhibition making"])
    add("Jorge Guevara", ["choreography", "contemporary dance", "Mexican performance"])
    
    # Remaining known but unattributed
    add("Selma Jeanne Cohen", ["dance history", "dance criticism", "documentary dance"])
    add("Paul Stubbs", ["social policy", "NGO sector", "post-Yugoslav civil society"])
    add("James Boggs", ["Black radical tradition", "automation and labor", "American revolution"])
    add("Dominic Johnson", ["live art", "queer performance", "body art history"])
    add("Piro Rexhepi", ["decolonial Islam", "queer Balkans", "Muslim studies"])
    add("Ana Hoffner", ["queer memory", "post-Yugoslav feminist practice", "archival performance"])
    add("Joanna Russ", ["feminist science fiction", "how to suppress women's writing", "utopian feminism"])
    add("Robert Morris", ["minimalism", "process art", "anti-form", "sculpture phenomenology"])
    add("D. Papadopoulos", ["molecular politics", "post-representational politics", "biopolitics"])
    add("Johanna Heddva", ["sick woman theory", "crip theory", "radical care"])
    add("Nikolas Rose", ["governmentality", "biopolitics", "psy-disciplines", "neuroscience and society"])
    add("Bruno, G.", ["haptic visuality", "cinematic geography", "surface and texture", "affect and film"])
    add("Tom Sparrow", ["new materialism", "phenomenology critique", "speculative realism"])
    add("Amanda Piña", ["decolonial choreography", "endangered movement practices", "indigenous performance"])
    add("Dominic Eichler", ["contemporary art criticism", "curatorial writing"])
    add("Graham Burchell", ["governmentality studies", "Foucault translation", "liberal arts"])
    add("Paul Rabinow", ["anthropology of reason", "biosociality", "ethics of inquiry"])
    add("Ingrid Vranken", ["contemporary dance", "performance studies", "body politics"])
    add("Dejan Sretenović", ["Yugoslav new art practice", "conceptual art criticism"])
    add("Fuad Muhić", ["Marxist aesthetics", "Yugoslav philosophy", "philosophy of art"])
    add("William Forsythe", ["deconstructed ballet", "choreographic objects", "improvisation technologies"])
    add("Andraž Šalamun", ["Slovenian poetry", "avant-garde poetics"])
    add("Gregor Dražil", ["performance art", "body art", "Slovenian performance"])
    add("Bojan Đorđev", ["performance theory", "Yugoslav performance", "feminist dramaturgy"])
    add("Vita Osojnik", ["noise music", "sound poetry", "experimental vocal performance"])
    add("Dušan Bošković", ["Yugoslav cinema", "film theory"])
    add("Elīna Drāke", ["contemporary dance", "choreography", "Baltic performance"])
    add("Leah Barclay", ["sound ecology", "acoustic ecology", "environmental sound art"])
    add("Barbara Borčić", ["video art", "media art", "Slovenian media culture"])
    add("Isabelle Stengers", ["philosophy of science", "cosmopolitics", "speculative pragmatism", "Whitehead"])
    add("K. Knorr Cetina", ["epistemic cultures", "laboratory studies", "science and technology studies"])
    add("Wendy Brown", ["neoliberalism critique", "waning of sovereignty", "wounded attachments", "political theory"])
    add("Mai Abu ElDahab", ["curatorial practice", "postcolonial exhibitions", "art mediation"])
    add("Susanne Leeb", ["art history", "primitivism", "postcolonial art history"])
    add("Lauri Siisiäinen", ["Foucault studies", "biopolitics", "Finnish philosophy"])
    add("Lauri Siisiäinen", ["Foucault studies", "biopolitics", "governmentality"])
    add("Juhan Viiding", ["Estonian poetry", "language play", "experimental verse"])
    add("Christopher Clark", ["Prussian history", "European history", "World War I origins", "sleepwalkers"])
    add("Maaike Bleeker", ["visuality in performance", "theatricality", "cognitive theatre studies"])
    add("Gerald Raunig", ["art factories", "molecular revolution", "European precariat"])
    add("Françoise Vergès", ["postcolonial studies", "French colonial memory", "feminism and race", "decolonizing museums"])
    add("Marion von Osten", ["modernity and colonialism", "migrant labor", "art and production"])
    add("Vilém Flusser", ["philosophy of photography", "technical images", "gesture", "communication theory"])
    add("Manuela Boatcă", ["world-systems analysis", "coloniality", "global inequalities"])
    add("Bonaventure Soh Bejeng Ndikung", ["curatorial practice", "sound art", "African contemporary art"])
    add("Yvonne Vera", ["Zimbabwean women's writing", "postcolonial trauma", "body and violence"])
    add("M'hamed Issiakhem", ["Algerian painting", "war trauma", "figurative expressionism"])
    add("Romuald Hazoumè", ["African contemporary art", "recycled materials", "oil economy critique"])
    add("David Attenborough", ["natural history documentary", "biodiversity", "climate communication"])
    add("Charlie Chaplin", ["silent film", "physical comedy", "social satire", "tramp figure"])
    add("Geoffrey Batchen", ["photography theory", "photography ontology", "photography history"])
    add("Richard Wright", ["African American literature", "naturalism", "existentialism", "protest fiction"])
    add("Marek Šindelka", ["Czech contemporary fiction", "dystopian prose"])
    add("Nikolaj Gogol", ["Russian literature", "satire", "grotesque realism", "ukrainian roots"])
    add("Stane Jagodič", ["Slovenian literature", "drama"])
    add("Josip Depolo", ["Yugoslav cultural history", "translation theory"])
    add("Aldo Milohnić", ["performing arts theory", "cultural policy", "biopolitics in theatre"])
    add("Ivan Cankar", ["Slovenian modernism", "social criticism", "symbolism", "Slovenian national identity"])
    add("Tone Stojko", ["Slovenian photography", "documentary photography", "socialist Yugoslavia"])
    
    if args.dry_run:
        print("Dry run — no KG writes performed.")
        return
    kg.save()
    print("\nDone.")


if __name__ == "__main__":
    main()
