---
type: research-capture
fetched_on: 2026-09-11
---

Intent: official Darwin Core identity/withholding and EML rights definitions; no data downloaded.

Darwin Core Quick Reference Guide - Darwin Core (https://dwc.tdwg.org/terms/)
citeturn12view0 [wordlim: 200] Crawled: today; Content type: text/html; Source: find({"ref_id":"https://dwc.tdwg.org/terms/","pattern":"informationWithheld"}); Total lines: 3342
L43: ## Record-level
L44: 
L45: This category contains terms that are generic in that they might apply to any type of record in a dataset.
L46: 
L47: cite41†type cite42†modified cite43†language cite44†license cite45†rightsHolder cite46†accessRights cite47†bibliographicCitation cite48†references cite49†feedbackURL cite50†institutionID cite51†collectionID cite52†datasetID cite53†institutionCode cite54†collectionCode cite55†ownerInstitutionCode cite56†datasetName cite57†basisOfRecord cite58†informationWithheld cite59†dataGeneralizations cite60†dynamicProperties L213: informationWithheld
L214: ---
L215: Identifier  | cite94†http://rs.tdwg.org/dwc/terms/informationWithheld†rs.tdwg.org L216: Definition  | Additional information that exists about a resource, but that is not shared publicly. Suggests that alternative data of higher quality may be available on request.
L217: Notes  | This term has an equivalent in the dwciri: namespace that allows only an IRI as a value, whereas this term allows for any string literal value.
L218: Examples  |
L3099: informationWithheld
L3100: ---
L3101: Identifier  | cite785†http://rs.tdwg.org/dwc/iri/informationWithheld†rs.tdwg.org L3102: Definition  | Additional information that exists about a resource, but that is not shared publicly. Suggests that alternative data of higher quality may be available on request.
L3103: Notes  | Terms in the dwciri: namespace are intended to be used in RDF with non-literal objects.
L3104: Examples  |
--------------------------------------------------------------------------------
Darwin Core Quick Reference Guide - Darwin Core (https://dwc.tdwg.org/terms/)
citeturn12view1 [wordlim: 200] Crawled: today; Content type: text/html; Source: find({"ref_id":"https://dwc.tdwg.org/terms/","pattern":"occurrenceID"}); Total lines: 3342
L1626: ## Occurrence
L1627: 
L1628: cite402†occurrenceID cite403†recordedBy cite404†recordedByID cite405†individualCount cite406†organismQuantity cite407†organismQuantityType cite408†sex cite409†lifeStage cite410†reproductiveCondition cite411†caste cite412†behavior cite413†vitality cite414†establishmentMeans cite415†degreeOfEstablishment cite416†pathway cite417†georeferenceVerificationStatus cite418†occurrenceStatus cite419†associatedMedia cite420†associatedOccurrences cite421†associatedReferences cite422†associatedTaxa cite423†occurrenceRemarks L1640: occurrenceID
L1641: ---
L1642: Identifier  | cite425†http://rs.tdwg.org/dwc/terms/occurrenceID†rs.tdwg.org L1643: Definition  | An identifier for a dwc:Occurrence (as opposed to a particular digital record of a dwc:Occurrence).
L1644: Notes  | In the absence of a persistent global unique identifier, construct one from a combination of identifiers in the record that will most closely make the dwc:occurrenceID globally unique.
L1645: Examples  |
--------------------------------------------------------------------------------
Schema documentation for eml-dataset.xsd (https://eml.ecoinformatics.org/schema/eml-dataset_xsd)
citeturn12view2 [wordlim: 200] Crawled: today; Content type: text/html; Source: find({"ref_id":"https://eml.ecoinformatics.org/schema/eml-dataset_xsd","pattern":"intellectualRights"}); Total lines: 1310
L977: Children cite153†abstract , cite155†additionalInfo , cite144†alternateIdentifier , cite160†annotation , cite162†article , cite149†associatedParty , cite173†audioVisual , cite175†bibtex , cite163†book , cite164†chapter , cite169†conferenceProceedings , cite161†contact , cite159†coverage , cite147†creator , cite158†distribution , cite165†editedBook , cite172†generic , cite156†intellectualRights , cite154†keywordSet , cite151†language , cite157†licensed , cite166†manuscript , cite171†map , cite148†metadataProvider , cite170†personalCommunication , cite174†presentation , cite150†pubDate , cite38†references , cite167†report , cite152†series , cite145†shortName , cite168†thesis , cite146†title L978: Instance [Input]
L979: 
L980:     <referencePublication id="" scope="document" system="">
L981:       <alternateIdentifier system="">{0,unbounded}</alternateIdentifier>
L982:       <shortName>{0,1}</shortName>
L983:       <title xml:lang="">{1,unbounded}</title>
L984:       <creator id="" scope="document" system="">{1,unbounded}</creator>
L985:       <metadataProvider id="" scope="document" system="">{0,unbounded}</metadataProvider>
L986:       <associatedParty id="" scope="document" system="">{0,unbounded}</associatedParty>
L987:       <pubDate>{0,1}</pubDate>
L988:       <language xml:lang="">{0,1}</language>
L989:       <series>{0,1}</series>
L990:       <abstract xml:lang="">{0,1}</abstract>
L991:       <keywordSet>{0,unbounded}</keywordSet>
L992:       <additionalInfo xml:lang="">{0,unbounded}</additionalInfo>
L993:       <intellectualRights xml:lang="">{0,1}</intellectualRights>
L994:       <licensed>{0,unbounded}</licensed>
L995:       <distribution id="" scope="document" system="">{0,unbounded}</distribution>
L996:       <coverage id="" scope="document" system="">{0,1}</coverage>
L997:       <annotation id="" scope="document" system="">{0,unbounded}</annotation>
L998:       <contact id="" scope="document" system="">{0,unbounded}</contact>
L999:       <article>{1,1}</article>
L1000:       <book>{1,1}</book>
L1001:       <chapter>{1,1}</chapter>
L1002:       <editedBook>{1,1}</editedBook>
L1003:       <manuscript>{1,1}</manuscript>
L1004:       <report>{1,1}</report>
L1005:       <thesis>{1,1}</thesis>
L1006:       <conferenceProceedings>{1,1}</conferenceProceedings>
L1047: Model[Input] (cite144†alternateIdentifier* , cite145†shortName{0,1} , cite146†title+ , cite147†creator+ , cite148†metadataProvider* , cite149†associatedParty* , cite150†pubDate{0,1} , cite151†language{0,1} , cite152†series{0,1} , cite153†abstract{0,1} , cite154†keywordSet* , cite155†additionalInfo* , cite156†intellectualRights{0,1} , cite157†licensed* , cite158†distribution* , cite159†coverage{0,1} , cite160†annotation* , cite161†contact* , (cite162†article | cite163†book | cite164†chapter | cite165†editedBook | cite166†manuscript | cite167†report | cite168†thesis | cite169†conferenceProceedings | cite170†personalCommunication | cite171†map | cite172†generic | cite173†audioVisual | cite174†presentation )) | cite175†bibtex | (cite38†references )
L1048: Children cite153†abstract , cite155†additionalInfo , cite144†alternateIdentifier , cite160†annotation , cite162†article , cite149†associatedParty , cite173†audioVisual , cite175†bibtex , cite163†book , cite164†chapter , cite169†conferenceProceedings , cite161†contact , cite159†coverage , cite147†creator , cite158†distribution , cite165†editedBook , cite172†generic , cite156†intellectualRights , cite154†keywordSet , cite151†language , cite157†licensed , cite166†manuscript , cite171†map , cite148†metadataProvider , cite170†personalCommunication , cite174†presentation , cite150†pubDate , cite38†references , cite167†report , cite152†series , cite145†shortName , cite168†thesis , cite146†title L1049: Instance [Input]
L1050: 
L1051:     <usageCitation id="" scope="document" system="">
L1052:       <alternateIdentifier system="">{0,unbounded}</alternateIdentifier>
L1053:       <shortName>{0,1}</shortName>
L1054:       <title xml:lang="">{1,unbounded}</title>
L1055:       <creator id="" scope="document" system="">{1,unbounded}</creator>
L1056:       <metadataProvider id="" scope="document" system="">{0,unbounded}</metadataProvider>
L1057:       <associatedParty id="" scope="document" system="">{0,unbounded}</associatedParty>
L1058:       <pubDate>{0,1}</pubDate>
L1059:       <language xml:lang="">{0,1}</language>
L1060:       <series>{0,1}</series>
L1061:       <abstract xml:lang="">{0,1}</abstract>
L1062:       <keywordSet>{0,unbounded}</keywordSet>
L1063:       <additionalInfo xml:lang="">{0,unbounded}</additionalInfo>
L1064:       <intellectualRights xml:lang="">{0,1}</intellectualRights>
L1065:       <licensed>{0,unbounded}</licensed>
L1066:       <distribution id="" scope="document" system="">{0,unbounded}</distribution>
L1067:       <coverage id="" scope="document" system="">{0,1}</coverage>
L1068:       <annotation id="" scope="document" system="">{0,unbounded}</annotation>
L1069:       <contact id="" scope="document" system="">{0,unbounded}</contact>
L1070:       <article>{1,1}</article>
L1071:       <book>{1,1}</book>
L1072:       <chapter>{1,1}</chapter>
L1073:       <editedBook>{1,1}</editedBook>
L1074:       <manuscript>{1,1}</manuscript>
L1075:       <report>{1,1}</report>
L1076:       <thesis>{1,1}</thesis>
L1077:       <conferenceProceedings>{1,1}</conferenceProceedings>
L1078:       <personalCommunication>{1,1}</personalCommunication>
L1079:       <map>{1,1}</map>
L1149: Model[Input] (cite144†alternateIdentifier* , cite145†shortName{0,1} , cite146†title+ , cite147†creator+ , cite148†metadataProvider* , cite149†associatedParty* , cite150†pubDate{0,1} , cite151†language{0,1} , cite152†series{0,1} , cite153†abstract{0,1} , cite154†keywordSet* , cite155†additionalInfo* , cite156†intellectualRights{0,1} , cite157†licensed* , cite158†distribution* , cite159†coverage{0,1} , cite160†annotation* , cite185†purpose{0,1} , cite186†introduction{0,1} , cite187†gettingStarted{0,1} , cite188†acknowledgements{0,1} , cite189†maintenance{0,1} , cite190†contact+ , cite191†publisher{0,1} , cite192†pubPlace{0,1} , cite193†methods{0,1} , cite194†project{0,1} , (cite195†dataTable | cite196†spatialRaster | cite197†spatialVector | cite198†storedProcedure | cite199†view | cite200†otherEntity ) , cite201†referencePublication{0,1} , cite202†usageCitation* , cite203†literatureCited* ) | (cite38†references )
L1150: Children cite153†abstract , cite188†acknowledgements , cite155†additionalInfo , cite144†alternateIdentifier , cite160†annotation , cite149†associatedParty , cite190†contact , cite159†coverage , cite147†creator , cite195†dataTable , cite158†distribution , cite187†gettingStarted , cite156†intellectualRights , cite186†introduction , cite154†keywordSet , cite151†language , cite157†licensed , cite203†literatureCited , cite189†maintenance , cite148†metadataProvider , cite193†methods , cite200†otherEntity , cite194†project , cite150†pubDate , cite192†pubPlace , cite191†publisher , cite185†purpose , cite201†referencePublication , cite38†references , cite152†series , cite145†shortName , cite196†spatialRaster , cite197†spatialVector , cite198†storedProcedure , cite146†title , cite202†usageCitation , cite199†view L1151: Instance [Input]
L1152: 
L1153:     <dataset id="" scope="document" system="" xmlns="https://eml.ecoinformatics.org/dataset-2.2.0">
L1154:       <alternateIdentifier system="">{0,unbounded}</alternateIdentifier>
L1155:       <shortName>{0,1}</shortName>
L1156:       <title xml:lang="">{1,unbounded}</title>
L1157:       <creator id="" scope="document" system="">{1,unbounded}</creator>
L1158:       <metadataProvider id="" scope="document" system="">{0,unbounded}</metadataProvider>
L1159:       <associatedParty id="" scope="document" system="">{0,unbounded}</associatedParty>
L1160:       <pubDate>{0,1}</pubDate>
L1161:       <language xml:lang="">{0,1}</language>
L1162:       <series>{0,1}</series>
L1163:       <abstract xml:lang="">{0,1}</abstract>
L1164:       <keywordSet>{0,unbounded}</keywordSet>
L1165:       <additionalInfo xml:lang="">{0,unbounded}</additionalInfo>
L1166:       <intellectualRights xml:lang="">{0,1}</intellectualRights>
L1167:       <licensed>{0,unbounded}</licensed>
L1168:       <distribution id="" scope="document" system="">{0,unbounded}</distribution>
L1169:       <coverage id="" scope="document" system="">{0,1}</coverage>
L1170:       <annotation id="" scope="document" system="">{0,unbounded}</annotation>
L1171:       <purpose xml:lang="">{0,1}</purpose>
L1172:       <introduction xml:lang="">{0,1}</introduction>
L1173:       <gettingStarted xml:lang="">{0,1}</gettingStarted>
L1174:       <acknowledgements xml:lang="">{0,1}</acknowledgements>
L1175:       <maintenance>{0,1}</maintenance>

